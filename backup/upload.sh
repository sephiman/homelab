#!/bin/bash
# Copies every local backup still marked .pending-upload to Google Drive, then prunes old Drive copies.
# Run by backup.sh right after a backup and hourly from cron; rclone skips files already on Drive,
# so a retry only sends what the last attempt missed.
set -euo pipefail

STAGING="/backups"
RCLONE_REMOTE="${RCLONE_REMOTE:-gdrive}"
RCLONE_PATH="${RCLONE_PATH:-homelab-backups}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-7}"
UPLOAD_TIMEOUT="${UPLOAD_TIMEOUT:-3h}"
METRICS_FILE=/metrics/offsite.prom
PENDING_MARKER=.pending-upload

# Drive answers bursts with 403 quota errors; throttle and retry instead of failing.
# 128M chunks cut a ~1 GB upload from ~124 requests to ~8; RAM use is chunk size x transfers.
RCLONE_FLAGS=(
  --tpslimit 5 --retries 3 --retries-sleep 1m --low-level-retries 20
  --drive-chunk-size 128M --transfers 2
  --exclude "${PENDING_MARKER}"
)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

previous_metric() {
  sed -n "s/^$1 //p" "${METRICS_FILE}" 2>/dev/null || true
}

pending_backups() {
  local dir
  for dir in $(ls -1dt "${STAGING}"/*/ 2>/dev/null); do
    [ -e "${dir}${PENDING_MARKER}" ] && echo "${dir%/}"
  done
  return 0
}

# Newest first: if Drive is flaky, the most valuable copy goes up before older stragglers.
upload_backup() {
  local dir=$1 target="${RCLONE_REMOTE}:${RCLONE_PATH}/$(basename "$1")"
  log "Uploading ${dir} to ${target} ..."
  timeout "${UPLOAD_TIMEOUT}" rclone copy "${dir}" "${target}" "${RCLONE_FLAGS[@]}" \
    --stats-one-line --stats=30s || return 1
  log "Verifying ${target} ..."
  rclone check "${dir}" "${target}" --one-way "${RCLONE_FLAGS[@]}" || return 1
  rm -f "${dir}/${PENDING_MARKER}"
  log "Uploaded and verified: ${target}"
}

prune_remote() {
  log "Pruning Google Drive backups older than ${REMOTE_RETENTION_DAYS} days..."
  rclone delete "${RCLONE_REMOTE}:${RCLONE_PATH}" --min-age "${REMOTE_RETENTION_DAYS}d" &&
    rclone rmdirs "${RCLONE_REMOTE}:${RCLONE_PATH}" --leave-root
}

# Runs on every exit; the upload time keeps the last backup that actually reached Drive.
write_metrics() {
  local status=$? now success last_upload pending
  [ -d "$(dirname "${METRICS_FILE}")" ] || return 0
  now="$(date +%s)"
  success=$(( status == 0 ? 1 : 0 ))
  last_upload="${LAST_UPLOAD:-$(previous_metric homelab_backup_offsite_last_upload_timestamp_seconds)}"
  pending="$(pending_backups | wc -l)"
  cat > "${METRICS_FILE}.tmp" <<EOF
# HELP homelab_backup_offsite_last_run_timestamp_seconds When the last upload run ended.
# TYPE homelab_backup_offsite_last_run_timestamp_seconds gauge
homelab_backup_offsite_last_run_timestamp_seconds ${now}
# HELP homelab_backup_offsite_last_run_success 1 if the last upload run uploaded everything and pruned Drive, 0 if not.
# TYPE homelab_backup_offsite_last_run_success gauge
homelab_backup_offsite_last_run_success ${success}
# HELP homelab_backup_offsite_last_upload_timestamp_seconds When a backup was last verified on Drive.
# TYPE homelab_backup_offsite_last_upload_timestamp_seconds gauge
homelab_backup_offsite_last_upload_timestamp_seconds ${last_upload:-0}
# HELP homelab_backup_offsite_pending Local backups not yet copied to Drive.
# TYPE homelab_backup_offsite_pending gauge
homelab_backup_offsite_pending ${pending}
EOF
  mv "${METRICS_FILE}.tmp" "${METRICS_FILE}"
}

exec 9> "${STAGING}/.upload.lock"
if ! flock -n 9; then
  log "Another upload is in progress; skipping this one."
  exit 0
fi

# Written every hour, even with nothing pending, so a past failure clears once it is resolved.
trap write_metrics EXIT
PENDING="$(pending_backups)"
[ -n "${PENDING}" ] || exit 0

failed=0
for dir in ${PENDING}; do
  if upload_backup "${dir}"; then
    LAST_UPLOAD="$(date +%s)"
  else
    log "FAILED upload: ${dir} (stays pending, retried next hour)"
    failed=1
  fi
done

# Only prune once everything is up, so Drive never loses old copies while new ones are missing.
[ "${failed}" -eq 0 ] || exit 1
prune_remote
