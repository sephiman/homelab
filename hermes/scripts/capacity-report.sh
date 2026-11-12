#!/usr/bin/env bash
# Hermes cron pre-run script, weekly: gathers disk, Docker and Postgres storage usage.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000

output="$(sudo -n /usr/local/bin/homelab-manage.sh capacity-report 2>&1)"
if [[ -n "$output" ]]; then
  printf '%s\n' "${output:0:$MAX_OUTPUT_BYTES}"
fi
