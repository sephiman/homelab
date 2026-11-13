#!/usr/bin/env bash
# Hermes cron pre-run script, hourly, one job per chat: job scripts take no arguments, so
# install-host.sh generates price-watch-<chat id>.sh, which calls this with its chat id. Prints that
# chat's AliExpress watches that reached their target, hit a new low, or disappeared since the last
# run, and an API failure once. Nothing new prints nothing, and Hermes then skips the model.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000
readonly CHAT="${1:?usage: price-watch.sh <telegram chat id>}"

output="$(/usr/local/bin/price-hunt check --chat "$CHAT" 2>&1)"
if [[ -n "$output" ]]; then
  printf '%s\n' "${output:0:$MAX_OUTPUT_BYTES}"
fi
