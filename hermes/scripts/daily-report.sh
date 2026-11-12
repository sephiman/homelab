#!/usr/bin/env bash
# Hermes cron pre-run script for the morning summary: its output is the model's only input (the
# cron platform has no tools, see config.yaml), so collect everything here.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000

output="$(sudo -n /usr/local/bin/homelab-manage.sh daily-report 2>&1)"
if [[ -n "$output" ]]; then
  printf '%s\n' "${output:0:$MAX_OUTPUT_BYTES}"
fi
