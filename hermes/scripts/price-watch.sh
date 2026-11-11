#!/usr/bin/env bash
# Hermes cron pre-run script, hourly: prints AliExpress watches that reached their target or
# disappeared since the last run, and an API failure once. Nothing new prints nothing, and Hermes
# then skips the model.
set -euo pipefail

readonly MAX_OUTPUT_BYTES=30000

/usr/local/bin/price-hunt check 2>&1 | head -c "$MAX_OUTPUT_BYTES"
