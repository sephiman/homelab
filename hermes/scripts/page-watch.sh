#!/usr/bin/env bash
# Hermes cron pre-run script, every 5 minutes, one job per chat: job scripts take no arguments, so
# install-host.sh generates page-watch-<chat id>.sh, which calls this with its chat id. Prints that
# chat's page watches whose condition is now met, reminders that are due, and watches that keep
# failing or change on every check. Nothing to say prints nothing, and Hermes then skips the model.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000
readonly CHAT="${1:?usage: page-watch.sh <telegram chat id>}"

output="$(/usr/local/bin/page-watch check --chat "$CHAT" 2>&1)"
if [[ -n "$output" ]]; then
  printf '%s\n' "${output:0:$MAX_OUTPUT_BYTES}"
fi
