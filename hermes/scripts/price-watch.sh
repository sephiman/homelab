#!/usr/bin/env bash
# Hermes cron pre-run script, hourly: prints AliExpress watches that reached their target, hit a
# new low, or disappeared since the last run, and an API failure once. Nothing new prints nothing,
# and Hermes then skips the model.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000

output="$(/usr/local/bin/price-hunt check 2>&1)"
[[ -n "$output" ]] && printf '%s\n' "${output:0:$MAX_OUTPUT_BYTES}"
