#!/bin/bash
set -e

CRON="${BACKUP_CRON:-5 3 * * *}"
LOG=/var/log/backup.log
touch "${LOG}"

# busybox crond runs jobs with a minimal environment, so persist the relevant
# variables to a file that the cron job sources before running the backup.
printenv \
  | grep -E '^(POSTGRES_|RCLONE_|LOCAL_KEEP=|REMOTE_RETENTION_|TZ=)' \
  | sed -E "s/^([^=]+)=(.*)$/export \1='\2'/" \
  > /etc/backup.env

cat > /etc/crontabs/root <<EOF
${CRON} . /etc/backup.env; /usr/local/bin/backup.sh >> ${LOG} 2>&1
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
