#!/bin/bash
set -euo pipefail

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
STAGING="/backups"
DEST="${STAGING}/${TIMESTAMP}"

POSTGRES_HOST="${POSTGRES_HOST:-postgresdb}"
RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
RCLONE_PATH="${RCLONE_PATH:-homelab-backups}"
LOCAL_KEEP="${LOCAL_KEEP:-1}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-7}"
PG_EXCLUDE_DATABASES="${PG_EXCLUDE_DATABASES:-}"
# Read by node-exporter's textfile collector; Prometheus alerts on failed or stale backups.
METRICS_FILE=/metrics/backup.prom

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# tar exit 1 = "file changed as we read it"; the archive is still usable, so only >=2 fails the run.
tarball() {
  local dest=$1; shift
  local rc=0
  tar czf "${dest}" "$@" || rc=$?
  if [ "${rc}" -gt 1 ]; then
    log "tar failed (exit ${rc}): $(basename "${dest}")"
    return "${rc}"
  fi
  [ "${rc}" -eq 1 ] && log "warning: files changed while archiving $(basename "${dest}") (archive kept)"
  return 0
}

previous_metric() {
  sed -n "s/^$1 //p" "${METRICS_FILE}" 2>/dev/null || true
}

# Runs on every exit, so a failed run is recorded too; success time and size keep the last success.
write_metrics() {
  local status=$? now success last_success size_bytes
  [ -d "$(dirname "${METRICS_FILE}")" ] || return 0
  now="$(date +%s)"
  if [ "${status}" -eq 0 ]; then
    success=1
    last_success="${now}"
    size_bytes="$(du -sb "${DEST}" | cut -f1)"
  else
    log "=== Backup FAILED (exit ${status}): ${TIMESTAMP} ==="
    success=0
    last_success="$(previous_metric homelab_backup_last_success_timestamp_seconds)"
    size_bytes="$(previous_metric homelab_backup_size_bytes)"
  fi
  cat > "${METRICS_FILE}.tmp" <<EOF
# HELP homelab_backup_last_run_timestamp_seconds When the last backup run ended.
# TYPE homelab_backup_last_run_timestamp_seconds gauge
homelab_backup_last_run_timestamp_seconds ${now}
# HELP homelab_backup_last_run_success 1 if the last backup run finished, 0 if it failed.
# TYPE homelab_backup_last_run_success gauge
homelab_backup_last_run_success ${success}
# HELP homelab_backup_last_success_timestamp_seconds When the last successful backup ended.
# TYPE homelab_backup_last_success_timestamp_seconds gauge
homelab_backup_last_success_timestamp_seconds ${last_success:-0}
# HELP homelab_backup_size_bytes Size of the last successful backup.
# TYPE homelab_backup_size_bytes gauge
homelab_backup_size_bytes ${size_bytes:-0}
EOF
  mv "${METRICS_FILE}.tmp" "${METRICS_FILE}"
}
trap write_metrics EXIT

log "=== Backup started: ${TIMESTAMP} ==="
mkdir -p "${DEST}"

# 1. Folder archives -------------------------------------------------------
# The live recorder DB + WAL can't be tarred consistently, so it is snapshotted separately.
log "Archiving homeassistant..."
tarball "${DEST}/homeassistant.tar.gz" --exclude='./config/home-assistant_v2.db*' -C /sources/homeassistant .

# VACUUM INTO reads in one transaction; the CLI's .backup restarts on every recorder commit.
log "Snapshotting Home Assistant database..."
sqlite3 /sources/homeassistant/config/home-assistant_v2.db "VACUUM INTO '${DEST}/homeassistant-db.sqlite'"
gzip "${DEST}/homeassistant-db.sqlite"

log "Archiving qbittorrent..."
tarball "${DEST}/qbittorrent.tar.gz" -C /sources/qbittorrent .

log "Archiving rustdesk..."
tarball "${DEST}/rustdesk.tar.gz" -C /sources/rustdesk .

# Jellyfin: only the config folder, not the media library or cache.
# metadata/ (~1 GB) is artwork and NFO data a library metadata refresh re-downloads.
log "Archiving jellyfin config..."
tarball "${DEST}/jellyfin-config.tar.gz" --exclude=./metadata --exclude=./log --exclude=./transcodes \
  -C /sources/jellyfin-config .

# Media apps: config + DB (+ the *arrs' own Backups/ zips); MediaCover is re-downloadable artwork.
for app in prowlarr sonarr radarr bazarr seerr cleanuparr maintainerr searcharr; do
  log "Archiving ${app} config..."
  tarball "${DEST}/${app}.tar.gz" --exclude=./MediaCover --exclude=./logs -C "/sources/${app}" .
done

# Vaultwarden: attachments, sends and rsa_key.pem (the vault itself is in PostgreSQL).
# icon_cache is just downloaded favicons.
log "Archiving vaultwarden data..."
tarball "${DEST}/vaultwarden.tar.gz" --exclude=./icon_cache -C /sources/vaultwarden .

# 2. PostgreSQL dump (all databases + roles) -------------------------------
# Build --exclude-database flags from PG_EXCLUDE_DATABASES (comma/space list).
EXCLUDE_ARGS=()
for db in ${PG_EXCLUDE_DATABASES//,/ }; do
  EXCLUDE_ARGS+=( "--exclude-database=${db}" )
done

if [ "${#EXCLUDE_ARGS[@]}" -gt 0 ]; then
  log "Dumping PostgreSQL from ${POSTGRES_HOST} (excluding: ${PG_EXCLUDE_DATABASES})..."
else
  log "Dumping PostgreSQL from ${POSTGRES_HOST}..."
fi

PGPASSWORD="${POSTGRES_PASSWORD}" pg_dumpall \
  -h "${POSTGRES_HOST}" \
  -U "${POSTGRES_USER}" \
  "${EXCLUDE_ARGS[@]}" \
  | gzip > "${DEST}/postgres-all.sql.gz"

# Checksums for integrity verification on restore.
( cd "${DEST}" && sha256sum ./*.tar.gz ./*.sqlite.gz ./*.sql.gz > SHA256SUMS )

log "Local backup ready: $(du -sh "${DEST}" | cut -f1)"

# 3. Upload to Google Drive ------------------------------------------------
log "Uploading to ${RCLONE_REMOTE}:${RCLONE_PATH}/${TIMESTAMP} ..."
# Drive answers bursts with 403 quota errors; throttle and retry instead of failing the run.
# 128M chunks cut a ~1 GB upload from ~124 requests to ~8; RAM use is chunk size x transfers.
rclone copy "${DEST}" "${RCLONE_REMOTE}:${RCLONE_PATH}/${TIMESTAMP}" \
  --tpslimit 5 --retries 5 --retries-sleep 1m --low-level-retries 20 \
  --drive-chunk-size 128M --transfers 2 \
  --stats-one-line --stats=30s

# 4. Local retention: keep only the most recent ${LOCAL_KEEP} backup(s) ----
log "Pruning local backups, keeping the most recent ${LOCAL_KEEP}..."
ls -1dt "${STAGING}"/*/ 2>/dev/null | tail -n "+$((LOCAL_KEEP + 1))" | xargs -r rm -rf

# 5. Remote retention ------------------------------------------------------
log "Pruning Google Drive backups older than ${REMOTE_RETENTION_DAYS} days..."
rclone delete "${RCLONE_REMOTE}:${RCLONE_PATH}" --min-age "${REMOTE_RETENTION_DAYS}d"
rclone rmdirs "${RCLONE_REMOTE}:${RCLONE_PATH}" --leave-root

log "=== Backup finished: ${TIMESTAMP} ==="
