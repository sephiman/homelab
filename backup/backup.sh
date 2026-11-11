#!/bin/bash
set -euo pipefail

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
STAGING="/backups"
DEST="${STAGING}/${TIMESTAMP}"

POSTGRES_HOST="${POSTGRES_HOST:-postgresdb}"
LOCAL_KEEP="${LOCAL_KEEP:-1}"
PG_EXCLUDE_DATABASES="${PG_EXCLUDE_DATABASES:-}"
# Read by node-exporter's textfile collector; Prometheus alerts on failed or stale backups.
METRICS_FILE=/metrics/backup.prom
# upload.sh picks up every folder holding this marker and removes it once Drive has a verified copy.
PENDING_MARKER=.pending-upload
# Headroom on top of the last backup's size, so a run never fills the disk halfway through.
FREE_SPACE_MARGIN_BYTES=$((2 * 1024 * 1024 * 1024))

FAILED_STEPS=()

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

previous_metric() {
  sed -n "s/^$1 //p" "${METRICS_FILE}" 2>/dev/null || true
}

# A failing step is recorded and skipped, so one broken source never costs the others their backup.
run_step() {
  local name=$1; shift
  log "${name}..."
  "$@" && return 0
  log "FAILED step: ${name}"
  FAILED_STEPS+=("${name}")
}

# tar exit 1 = "file changed as we read it"; the archive is still usable, so only >=2 fails.
archive() {
  local dest=$1; shift
  local rc=0
  tar czf "${dest}" "$@" || rc=$?
  if [ "${rc}" -gt 1 ]; then
    rm -f "${dest}"
    return "${rc}"
  fi
  [ "${rc}" -eq 1 ] && log "warning: files changed while archiving $(basename "${dest}") (archive kept)"
  return 0
}

# The live recorder DB + WAL can't be tarred consistently; VACUUM INTO reads in one transaction,
# while the CLI's .backup restarts on every recorder commit.
snapshot_homeassistant_db() {
  local snapshot="${DEST}/homeassistant-db.sqlite"
  sqlite3 /sources/homeassistant/config/home-assistant_v2.db "VACUUM INTO '${snapshot}'" &&
    gzip "${snapshot}" && return 0
  rm -f "${snapshot}" "${snapshot}.gz"
  return 1
}

dump_postgres() {
  local dest="${DEST}/postgres-all.sql.gz" exclude_args=() db
  for db in ${PG_EXCLUDE_DATABASES//,/ }; do
    exclude_args+=("--exclude-database=${db}")
  done
  PGPASSWORD="${POSTGRES_PASSWORD}" pg_dumpall -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" \
    "${exclude_args[@]}" | gzip > "${dest}" && return 0
  rm -f "${dest}"
  return 1
}

require_free_space() {
  local last_size free needed
  last_size="$(previous_metric homelab_backup_size_bytes)"
  free="$(df -Pk "${STAGING}" | awk 'NR == 2 { print $4 * 1024 }')"
  needed=$(( ${last_size:-0} + FREE_SPACE_MARGIN_BYTES ))
  [ "${free}" -ge "${needed}" ] && return 0
  log "Not enough free space in ${STAGING}: $((free / 1048576)) MiB free, $((needed / 1048576)) MiB needed"
  return 1
}

# Folders still waiting for upload are never pruned: they may be the only copy.
prune_uploaded_backups() {
  local dir
  for dir in $(ls -1dt "${STAGING}"/*/ 2>/dev/null); do
    [ -e "${dir}${PENDING_MARKER}" ] || echo "${dir}"
  done | tail -n "+$((LOCAL_KEEP + 1))" | xargs -r rm -rf
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
# HELP homelab_backup_last_run_timestamp_seconds When the last local backup run ended.
# TYPE homelab_backup_last_run_timestamp_seconds gauge
homelab_backup_last_run_timestamp_seconds ${now}
# HELP homelab_backup_last_run_success 1 if every step of the last local backup run succeeded, 0 if not.
# TYPE homelab_backup_last_run_success gauge
homelab_backup_last_run_success ${success}
# HELP homelab_backup_last_success_timestamp_seconds When the last fully successful local backup ended.
# TYPE homelab_backup_last_success_timestamp_seconds gauge
homelab_backup_last_success_timestamp_seconds ${last_success:-0}
# HELP homelab_backup_size_bytes Size of the last successful backup.
# TYPE homelab_backup_size_bytes gauge
homelab_backup_size_bytes ${size_bytes:-0}
EOF
  mv "${METRICS_FILE}.tmp" "${METRICS_FILE}"
}

# Shared through the /backups volume, so it also covers a `docker compose run` beside the scheduler.
exec 9> "${STAGING}/.backup.lock"
if ! flock -n 9; then
  log "Another backup run is in progress; skipping this one."
  exit 0
fi
trap write_metrics EXIT

log "=== Backup started: ${TIMESTAMP} ==="
require_free_space
mkdir -p "${DEST}"

# 1. Folder archives -------------------------------------------------------
run_step "Archiving homeassistant" \
  archive "${DEST}/homeassistant.tar.gz" --exclude='./config/home-assistant_v2.db*' -C /sources/homeassistant .
run_step "Snapshotting Home Assistant database" snapshot_homeassistant_db
run_step "Archiving qbittorrent" archive "${DEST}/qbittorrent.tar.gz" -C /sources/qbittorrent .
run_step "Archiving rustdesk" archive "${DEST}/rustdesk.tar.gz" -C /sources/rustdesk .

# Jellyfin: only the config folder, not the media library or cache.
# metadata/ (~1 GB) is artwork and NFO data a library metadata refresh re-downloads.
run_step "Archiving jellyfin config" \
  archive "${DEST}/jellyfin-config.tar.gz" --exclude=./metadata --exclude=./log --exclude=./transcodes \
  -C /sources/jellyfin-config .

# Media apps: config + DB (+ the *arrs' own Backups/ zips); MediaCover is re-downloadable artwork.
for app in prowlarr sonarr radarr bazarr seerr cleanuparr maintainerr searcharr; do
  run_step "Archiving ${app} config" \
    archive "${DEST}/${app}.tar.gz" --exclude=./MediaCover --exclude=./logs -C "/sources/${app}" .
done

# Vaultwarden: attachments, sends and rsa_key.pem (the vault itself is in PostgreSQL).
# icon_cache is just downloaded favicons.
run_step "Archiving vaultwarden data" \
  archive "${DEST}/vaultwarden.tar.gz" --exclude=./icon_cache -C /sources/vaultwarden .

# Calibre-Web Automated: config (app.db, Kobo tokens/shelves) + library (metadata.db + books).
# ingest is a transient watch folder.
run_step "Archiving calibre" \
  archive "${DEST}/calibre.tar.gz" --exclude=./ingest --exclude=./config/log_archive -C /sources/calibre .

# 2. PostgreSQL dump (all databases + roles) -------------------------------
run_step "Dumping PostgreSQL from ${POSTGRES_HOST}${PG_EXCLUDE_DATABASES:+ (excluding: ${PG_EXCLUDE_DATABASES})}" \
  dump_postgres

# Checksums for integrity verification on restore; covers whatever the steps produced.
( cd "${DEST}" && sha256sum -- *.gz > SHA256SUMS )
log "Local backup ready: $(du -sh "${DEST}" | cut -f1)"

# 3. Offsite copy ------------------------------------------------------------
# A partial backup is still worth shipping. If the upload fails, the hourly upload job retries it.
touch "${DEST}/${PENDING_MARKER}"
/usr/local/bin/upload.sh || log "Upload did not complete; the hourly upload job will retry it."

# 4. Local retention: keep the most recent ${LOCAL_KEEP} uploaded backup(s) ---
log "Pruning local backups, keeping the most recent ${LOCAL_KEEP} uploaded..."
prune_uploaded_backups

if [ "${#FAILED_STEPS[@]}" -gt 0 ]; then
  log "Failed steps: $(IFS=';'; echo "${FAILED_STEPS[*]}")"
  exit 1
fi
log "=== Backup finished: ${TIMESTAMP} ==="
