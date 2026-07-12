#!/bin/bash
set -e

# If a command is passed (e.g. `rclone config`, `backup.sh`, `sh`), run it
# directly instead of starting the scheduler. This is what lets the one-time
# `docker compose run --rm backup rclone config` work.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

CRON="${BACKUP_CRON:-5 3 * * *}"
LOG=/var/log/backup.log
touch "${LOG}"

# busybox crond runs jobs with a minimal environment, so persist the relevant
# variables to a file that the cron job sources before running the backup.
# `export -p` quotes values safely (quotes, spaces, ...), unlike hand-rolled
# sed quoting; the emitted `declare -x` lines require bash, hence `bash -c`.
export -p \
  | grep -E '^declare -x (POSTGRES_|PG_EXCLUDE_DATABASES=|RCLONE_|LOCAL_KEEP=|REMOTE_RETENTION_|TZ=)' \
  > /etc/backup.env

cat > /etc/crontabs/root <<EOF
${CRON} bash -c '. /etc/backup.env; /usr/local/bin/backup.sh' >> ${LOG} 2>&1
EOF

echo "[entrypoint] Backup scheduler started."
echo "[entrypoint]   schedule: ${CRON}"
echo "[entrypoint]   remote:   ${RCLONE_REMOTE:-gdrive}:${RCLONE_PATH:-homelab-backups}"
echo "[entrypoint]   retention: keep ${LOCAL_KEEP:-1} local / ${REMOTE_RETENTION_DAYS:-7}d on remote"

if [ "${RUN_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] RUN_ON_START=true -> running an initial backup now"
  /usr/local/bin/backup.sh >> "${LOG}" 2>&1 || echo "[entrypoint] initial backup FAILED (see log)"
fi

crond -b -l 8
exec tail -F "${LOG}"
