#!/usr/bin/env bash
# Hermes cron pre-run script for the morning summary: its output is the model's only input (the
# cron platform has no tools, see config.yaml), so collect everything here.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000

sudo -n /usr/local/bin/homelab-manage.sh daily-report 2>&1 | head -c "$MAX_OUTPUT_BYTES"
