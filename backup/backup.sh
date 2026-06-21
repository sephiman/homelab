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

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "=== Backup started: ${TIMESTAMP} ==="
mkdir -p "${DEST}"

# 1. Folder archives -------------------------------------------------------
log "Archiving homeassistant..."
tar czf "${DEST}/homeassistant.tar.gz" -C /sources/homeassistant .

log "Archiving qbittorrent..."
tar czf "${DEST}/qbittorrent.tar.gz" -C /sources/qbittorrent .

log "Archiving rustdesk..."
tar czf "${DEST}/rustdesk.tar.gz" -C /sources/rustdesk .

# Jellyfin: only the config folder, not the media library or cache.
log "Archiving jellyfin config..."
tar czf "${DEST}/jellyfin-config.tar.gz" -C /sources/jellyfin-config .

# 2. PostgreSQL dump (all databases + roles) -------------------------------
log "Dumping PostgreSQL from ${POSTGRES_HOST}..."
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dumpall \
  -h "${POSTGRES_HOST}" \
  -U "${POSTGRES_USER}" \
  | gzip > "${DEST}/postgres-all.sql.gz"

# Checksums for integrity verification on restore.
( cd "${DEST}" && sha256sum ./*.tar.gz ./*.sql.gz > SHA256SUMS )

log "Local backup ready: $(du -sh "${DEST}" | cut -f1)"

# 3. Upload to Google Drive ------------------------------------------------
log "Uploading to ${RCLONE_REMOTE}:${RCLONE_PATH}/${TIMESTAMP} ..."
rclone copy "${DEST}" "${RCLONE_REMOTE}:${RCLONE_PATH}/${TIMESTAMP}" --stats-one-line --stats=30s

# 4. Local retention: keep only the most recent ${LOCAL_KEEP} backup(s) ----
log "Pruning local backups, keeping the most recent ${LOCAL_KEEP}..."
ls -1dt "${STAGING}"/*/ 2>/dev/null | tail -n "+$((LOCAL_KEEP + 1))" | xargs -r rm -rf

# 5. Remote retention ------------------------------------------------------
log "Pruning Google Drive backups older than ${REMOTE_RETENTION_DAYS} days..."
rclone delete "${RCLONE_REMOTE}:${RCLONE_PATH}" --min-age "${REMOTE_RETENTION_DAYS}d"
rclone rmdirs "${RCLONE_REMOTE}:${RCLONE_PATH}" --leave-root

log "=== Backup finished: ${TIMESTAMP} ==="
