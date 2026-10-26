#!/usr/bin/env bash
# Hermes cron pre-run script, every few minutes: prints alerts that started firing since the last
# run plus context for diagnosing them. No new alert prints nothing, and Hermes then skips the model.
set -euo pipefail

readonly STATE_DIR="${HERMES_HOME:-${HOME:-/home/hermes}/.hermes}/state"
readonly SEEN_FILE="$STATE_DIR/alerts-seen"
readonly MAX_OUTPUT_BYTES=30000

manage() {
  sudo -n /usr/local/bin/homelab-manage.sh "$@" 2>&1
}

# An alert is the same one while its name, start time and labels don't change.
alert_key() {
  awk -F'\t' '{print $2 "|" $4 "|" $6}'
}

section() {
  printf '\n=== %s\n' "$1"
}

# Containers named in the alert's labels (cadvisor `name`, docker_sd `instance`, …).
alert_containers() {
  grep -oE '(^|,)(name|instance|container)=[A-Za-z0-9][A-Za-z0-9_.-]*' | cut -d= -f2 | sort -u
}

container_context() {
  local container="$1" project
  manage ps | awk -v c="$container" '$1 == c {found = 1} END {exit !found}' || return 0
  section "container $container"
  manage inspect "$container"
  project="$(manage inspect "$container" | awk '$1 == "project:" {print $2}')"
  if [[ -n "$project" ]]; then
    section "logs of $project (last 60 lines)"
    manage logs "$project" 60
  fi
}

alert_specific_context() {
  case "$1" in
    Backup*) section "backup-status"; manage backup-status ;;
    Arr*) section "media-health"; manage media-health ;;
    HomeAssistant* | Mosquitto*) section "ha-log (last 80)"; manage ha-log 80 ;;
    Host*)
      section "host"
      uptime
      free -h
      df -h -x tmpfs -x overlay
      top -bn1 | head -n 15
      ;;
  esac
}

# Firing alerts as `alerts` prints them. If Prometheus can't be queried, that is reported as an alert
# of its own (fixed start time, so once), rather than failing every run and waking the model each time.
firing_alerts() {
  local alerts
  if ! alerts="$(manage alerts)"; then
    printf 'firing\tAlertWatcherCannotQueryPrometheus\tcritical\t-\t%s\t\n' "$(head -n 1 <<<"$alerts")"
    return 0
  fi
  awk -F'\t' '$1 == "firing"' <<<"$alerts"
}

main() {
  local firing keys new line name labels container
  mkdir -p "$STATE_DIR"
  touch "$SEEN_FILE"
  firing="$(firing_alerts)"
  keys="$(alert_key <<<"$firing" | grep -v '^||$' || true)"
  new="$(grep -vxFf "$SEEN_FILE" <<<"$keys" || true)"
  # Only what fires now is remembered, so an alert that resolves and fires again is reported again.
  printf '%s\n' "$keys" >"$SEEN_FILE"
  [[ -n "$new" ]] || return 0

  section "new firing alerts (state, name, severity, active since, summary, labels)"
  while IFS= read -r line; do
    if grep -qxF "$(alert_key <<<"$line")" <<<"$new"; then
      echo "$line"
    fi
  done <<<"$firing"
  while IFS='|' read -r name _ labels; do
    alert_specific_context "$name"
    while IFS= read -r container; do
      [[ -n "$container" ]] && container_context "$container"
    done < <(alert_containers <<<"$labels")
  done <<<"$new"
  section "errors in the last hour, all projects"
  manage loki-errors "" 1 | tail -n 40
}

main | head -c "$MAX_OUTPUT_BYTES"
