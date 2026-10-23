#!/usr/bin/env bash
# The only privileged entry point Hermes has on the host (sudoers allows this file and nothing else).
# Works on any Docker Compose project that has containers on this host (running or stopped); the
# project's folder and compose files come from the labels Compose puts on its containers.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly DEFAULT_LINES=100
readonly HA_URL=http://127.0.0.1:8123
readonly HA_CONTAINER=homeassistant
readonly DEFAULT_LOGBOOK_HOURS=24
readonly MAX_LOGBOOK_HOURS=168
readonly LOKI_URL=http://127.0.0.1:3100
readonly PROMETHEUS_URL=http://127.0.0.1:9090
readonly DEFAULT_SEARCH_HOURS=24
readonly MAX_SEARCH_HOURS=720  # Loki keeps 30 days
readonly MAX_QUERY_CHARS=500
readonly ERROR_PATTERN='(?i)(error|exception|fatal|panic|traceback)'
# Full "Authorization: Bearer …" line, root-only, passed to curl as a file so the token never
# shows up in a process list.
readonly HA_AUTH_HEADER_FILE=/etc/homelab-manage/ha_auth_header
# Attributes that can carry secrets or the home's location never leave the wrapper.
readonly HA_HIDDEN_ATTRIBUTES='token|password|secret|key|picture|latitude|longitude|gps'
readonly MAX_LINES=500
readonly MAX_CODE_BYTES=200000
# Committed by mistake or not, files named like secrets are never shown.
readonly SECRET_PATH_PATTERN='(^|/)(\.env[^/]*|[^/]*\.(pem|key|p12|pfx|jks|keystore)|id_[a-z0-9]+|[^/]*(secret|credential|password)[^/]*)$'
# Fixed template so `inspect` never prints Config.Env (passwords, tokens, DATABASE_URL).
readonly INSPECT_FORMAT='{{.Name}}
  project:  {{index .Config.Labels "com.docker.compose.project"}}
  state:    {{.State.Status}} (started {{.State.StartedAt}}, exit {{.State.ExitCode}})
  health:   {{with index .State "Health"}}{{.Status}}{{else}}no healthcheck{{end}}
  restarts: {{.RestartCount}}
  image:    {{.Config.Image}}
  networks: {{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}
  ports:    {{range $port, $bindings := .NetworkSettings.Ports}}{{$port}}{{if $bindings}}->{{range $bindings}}{{.HostIp}}:{{.HostPort}} {{end}}{{end}} {{end}}
  mounts:   {{range .Mounts}}{{.Source}}:{{.Destination}}{{if not .RW}}:ro{{end}} {{end}}'

usage() {
  cat >&2 <<'EOF'
usage: sudo -n homelab-manage.sh <action> [args]
  projects                     Compose projects on this host and their folders
  ps                           all containers with status and image
  status  <project>            docker compose ps for one project
  logs    <project> [lines]    last log lines of a project (default 100, max 500)
  inspect <container>          state, health, image, networks, ports, mounts (never env)
  journal [lines]              docker.service journal (default 100, max 500)
  restart <project>            docker compose restart
  git-status <project>         branch, commits behind/ahead of the remote, last commits, local changes
  git-pull <project>           git pull --ff-only only (no rebuild; use update to redeploy)
  code-ls <project> [path]     files committed in the project's folder (git HEAD)
  code-show <project> <path>   one committed file (max 200 KB; secret-looking files refused)
  code-grep <project> <text>   committed lines containing text (fixed string, max 300 lines)
  update  <project>            git pull --ff-only (if a git checkout), compose pull, up -d --build
  restart-docker               systemctl restart docker (restarts everything)
  ha-states [domain]           Home Assistant entities with state (optionally one domain: light, sensor…)
  ha-state <entity_id>         one Home Assistant entity with its attributes (secrets/location hidden)
  ha-automations               automations: state, last triggered, name (spot off/unavailable ones)
  ha-log [lines]               Home Assistant container log (default 100, max 500)
  ha-logbook [hours] [entity_id]   what changed when and what caused it (default 24h, max 168h; last 500)
  ha-light <on|off|toggle> <light.entity>   switch a light — the only Home Assistant write allowed
  loki-search <project> <text> [hours]   log lines containing text, any container of a project (default 24h, max 720h; newest 500)
  loki-errors [project] [hours]          error/exception/fatal/panic lines, one project or all (same limits)
  metrics <promql>             one Prometheus instant query (max 500 chars)
  alerts                       Prometheus alerts currently pending or firing
EOF
  exit 2
}

die() {
  echo "homelab-manage: $*" >&2
  exit 2
}

line_count() {
  local lines="${1:-$DEFAULT_LINES}"
  [[ "$lines" =~ ^[0-9]{1,6}$ ]] || die "lines must be a number"
  if (( lines > MAX_LINES )); then
    lines=$MAX_LINES
  fi
  echo "$lines"
}

project_label() {
  local project="$1" label="$2"
  docker ps --all --filter "label=com.docker.compose.project=$project" \
    --format "{{.Label \"$label\"}}" | head -n 1
}

# Sets PROJECT_DIR and CONFIG_FILES for a project name, or dies.
resolve_project() {
  local project="$1"
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || die "invalid project name '$project'"
  PROJECT_DIR="$(project_label "$project" com.docker.compose.project.working_dir)"
  CONFIG_FILES="$(project_label "$project" com.docker.compose.project.config_files)"
  [[ -n "$PROJECT_DIR" && -n "$CONFIG_FILES" ]] || die "no compose project '$project' (see: projects)"
  [[ -d "$PROJECT_DIR" ]] || die "folder of '$project' is gone: $PROJECT_DIR"
}

dir_owner() {
  stat -c %U "$1"
}

home_of() {
  getent passwd "$1" | cut -d: -f6
}

# Runs as the project folder's owner (who must be in the docker group): compose files interpolate
# ${HOME} for bind mounts, and as root with the owner's HOME, buildx left root-owned files in the
# owner's ~/.docker that broke their own `docker compose build`.
compose() {
  local project="$1"
  shift
  local file_args=() files file
  IFS=',' read -ra files <<<"$CONFIG_FILES"
  for file in "${files[@]}"; do
    file_args+=(--file "$file")
  done
  (cd "$PROJECT_DIR" && as_owner docker compose --project-name "$project" "${file_args[@]}" "$@")
}

# git and compose run as the folder's owner so nothing they write ends up root-owned.
# runuser instead of sudo: Ubuntu's sudo-rs doesn't take every flag classic sudo does.
as_owner() {
  local owner
  owner="$(dir_owner "$PROJECT_DIR")"
  runuser -u "$owner" -- env HOME="$(home_of "$owner")" "$@"
}

# Never wait for a password or passphrase prompt: nobody is there to answer it.
owner_git() {
  as_owner env GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes' git -C "$PROJECT_DIR" "$@"
}

is_git_checkout() {
  owner_git rev-parse --is-inside-work-tree &>/dev/null
}

git_pull() {
  if ! is_git_checkout; then
    echo "not a git checkout, skipping git pull: $PROJECT_DIR"
    return 0
  fi
  owner_git pull --ff-only
}

git_status() {
  is_git_checkout || die "not a git checkout: $PROJECT_DIR"
  owner_git fetch --quiet || echo "warning: git fetch failed; remote state below may be stale"
  owner_git status --short --branch
  echo "--- last commits"
  owner_git log --oneline --decorate -5
  echo "--- incoming (not pulled yet)"
  owner_git log --oneline 'HEAD..@{upstream}' 2>/dev/null || echo "no upstream branch configured"
}

# Code is read from git's HEAD, never the working tree: gitignored files (.env…) are unreachable.
require_code_path() {
  [[ "$1" =~ ^[A-Za-z0-9._/@+-]{1,300}$ && "$1" != /* && "$1" != *..* ]] || die "invalid path '$1'"
  [[ ! "$1" =~ $SECRET_PATH_PATTERN ]] || die "refusing to show a secret-looking file: $1"
}

code_ls() {
  is_git_checkout || die "not a git checkout: $PROJECT_DIR"
  owner_git ls-tree -r --name-only HEAD -- "${1:-.}" | grep -Ev "$SECRET_PATH_PATTERN" | head -n 1000
}

code_show() {
  is_git_checkout || die "not a git checkout: $PROJECT_DIR"
  owner_git show "HEAD:./$1" | head -c "$MAX_CODE_BYTES"
}

code_grep() {
  is_git_checkout || die "not a git checkout: $PROJECT_DIR"
  # Lines are HEAD:<path>:<line>:<text>; the secret filter applies to <path>.
  owner_git grep -n -I -F -e "$1" HEAD -- . |
    secret="$SECRET_PATH_PATTERN" awk -F: '$2 !~ ENVIRON["secret"]' | head -n 300 || true
}

require_ha_token() {
  [[ -r "$HA_AUTH_HEADER_FILE" ]] || die "Home Assistant access not configured (HA_TOKEN in hermes/.env)"
}

ha_get() {
  require_ha_token
  curl -fsS --max-time 15 -H "@$HA_AUTH_HEADER_FILE" "$HA_URL/api/$1"
}

# The only Home Assistant service the wrapper ever calls; the entity is validated as light.* first.
ha_light() {
  local action="$1" entity="$2"
  require_ha_token
  curl -fsS --max-time 15 -X POST -H "@$HA_AUTH_HEADER_FILE" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg entity "$entity" '{entity_id: $entity}')" \
    "$HA_URL/api/services/light/$action" >/dev/null
  ha_get "states/$entity" | jq -r '"\(.entity_id): \(.state)"'
}

# Timestamps come from jq rather than `date -d`, whose relative-date parsing differs between GNU
# coreutils and the uutils that Ubuntu 26.04 ships.
ha_logbook() {
  local hours="$1" entity="$2" start end query
  start="$(jq -rn --argjson hours "$hours" 'now - $hours * 3600 | strftime("%Y-%m-%dT%H:%M:%SZ")')"
  end="$(jq -rn 'now | strftime("%Y-%m-%dT%H:%M:%SZ")')"
  query="end_time=$end"
  if [[ -n "$entity" ]]; then
    query+="&entity=$entity"
  fi
  ha_get "logbook/$start?$query" | jq -r '
    .[]
    | [.when, (.entity_id // ""), (.state // .message // ""), (.name // ""),
       ("by: " + (.context_name // .context_entity_id // .context_event_type // "-"))]
    | @tsv' | tail -n "$MAX_LINES"
}

hours_arg() {
  local hours="${1:-$DEFAULT_SEARCH_HOURS}"
  [[ "$hours" =~ ^[0-9]{1,4}$ && "$hours" -ge 1 ]] || die "hours must be a number"
  if (( hours > MAX_SEARCH_HOURS )); then
    hours=$MAX_SEARCH_HOURS
  fi
  echo "$hours"
}

require_project_name() {
  [[ "$1" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || die "invalid project name '$1'"
}

# The wrapper builds the LogQL itself (label selector + line filter) so a query can't be made
# arbitrarily heavy; jq's tojson quotes the search text as a LogQL string.
loki_query() {
  local logql="$1" hours="$2" start
  start="$(jq -rn --argjson hours "$hours" 'now - $hours * 3600 | strftime("%Y-%m-%dT%H:%M:%SZ")')"
  curl -fsS --max-time 30 -G "$LOKI_URL/loki/api/v1/query_range" \
    --data-urlencode "query=$logql" --data-urlencode "start=$start" \
    --data-urlencode "limit=$MAX_LINES" --data-urlencode "direction=backward" |
    jq -r '[.data.result[] | .stream.container as $container | .values[] | [.[0], $container, .[1]]]
      | sort_by(.[0])[]
      | "\(.[0] | tonumber / 1e9 | floor | strftime("%Y-%m-%dT%H:%M:%SZ"))  \(.[1])  \(.[2])"'
}

loki_search() {
  local project="$1" text="$2" hours="$3" logql
  logql="$(jq -rn --arg project "$project" --arg text "$text" \
    '"{compose_project=" + ($project | tojson) + "} |= " + ($text | tojson)')"
  loki_query "$logql" "$hours"
}

loki_errors() {
  local project="$1" hours="$2" selector logql
  if [[ -n "$project" ]]; then
    selector="$(jq -rn --arg project "$project" '"{compose_project=" + ($project | tojson) + "}"')"
  else
    selector='{job="docker"}'
  fi
  logql="$selector |~ $(jq -rn --arg pattern "$ERROR_PATTERN" '$pattern | tojson')"
  loki_query "$logql" "$hours"
}

# Prometheus' query endpoint is read-only, so free-form PromQL is fine; length and timeout are capped.
prometheus_query() {
  curl -fsS --max-time 20 -G "$PROMETHEUS_URL/api/v1/query" \
    --data-urlencode "query=$1" --data-urlencode "timeout=15s" |
    jq -r '.data.resultType as $type | .data.result
      | if $type == "vector" then .[] | "\(.metric | to_entries | map("\(.key)=\(.value)") | join(","))  \(.value[1])"
        elif $type == "matrix" then .[] | "\(.metric | to_entries | map("\(.key)=\(.value)") | join(","))  \(.values | length) samples, last \(.values[-1][1])"
        else tojson end' | head -n 200
}

prometheus_alerts() {
  curl -fsS --max-time 15 "$PROMETHEUS_URL/api/v1/alerts" |
    jq -r '.data.alerts[] | [.state, .labels.alertname, (.labels.severity // "-"), .activeAt, (.annotations.summary // "")] | @tsv'
}

ha_automations() {
  ha_get states | jq -r '
    .[]
    | select(.entity_id | startswith("automation."))
    | [.entity_id, .state, (.attributes.last_triggered // "never"), (.attributes.friendly_name // "")]
    | @tsv' | sort
}

ha_states() {
  local domain="$1"
  ha_get states | jq -r --arg domain "$domain" '
    .[]
    | select($domain == "" or (.entity_id | startswith($domain + ".")))
    | [.entity_id, .state, (.attributes.unit_of_measurement // ""), (.attributes.friendly_name // "")]
    | @tsv' | sort
}

ha_state() {
  ha_get "states/$1" | jq --arg hidden "$HA_HIDDEN_ATTRIBUTES" '{
    entity_id, state, last_changed,
    attributes: (.attributes | with_entries(select(.key | test($hidden; "i") | not)))
  }'
}

hold_lock() {
  exec 9>/run/homelab-manage.lock
  flock -w 600 9 || die "another restart/update is still running"
}

[[ $# -ge 1 ]] || usage

logger -t homelab-manage -- "by=${SUDO_USER:-$USER} args=$*"

action="$1"
shift
case "$action" in
  projects)
    [[ $# -eq 0 ]] || usage
    docker compose ls --all
    ;;
  ps)
    [[ $# -eq 0 ]] || usage
    docker ps --all --format 'table {{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Status}}\t{{.Image}}'
    ;;
  status)
    [[ $# -eq 1 ]] || usage
    resolve_project "$1"
    compose "$1" ps --all
    ;;
  logs)
    [[ $# -eq 1 || $# -eq 2 ]] || usage
    resolve_project "$1"
    lines="$(line_count "${2:-}")"
    compose "$1" logs --no-color --timestamps --tail "$lines"
    ;;
  inspect)
    [[ $# -eq 1 ]] || usage
    [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$ ]] || die "invalid container name"
    docker inspect --type container --format "$INSPECT_FORMAT" -- "$1"
    ;;
  journal)
    [[ $# -le 1 ]] || usage
    lines="$(line_count "${1:-}")"
    journalctl --unit docker.service --no-pager --lines "$lines"
    ;;
  restart)
    [[ $# -eq 1 ]] || usage
    resolve_project "$1"
    hold_lock
    compose "$1" restart
    ;;
  git-pull)
    [[ $# -eq 1 ]] || usage
    resolve_project "$1"
    is_git_checkout || die "not a git checkout: $PROJECT_DIR"
    hold_lock
    git_pull
    ;;
  code-ls)
    [[ $# -eq 1 || $# -eq 2 ]] || usage
    resolve_project "$1"
    if [[ -n "${2:-}" ]]; then
      require_code_path "$2"
    fi
    code_ls "${2:-}"
    ;;
  code-show)
    [[ $# -eq 2 ]] || usage
    resolve_project "$1"
    require_code_path "$2"
    code_show "$2"
    ;;
  code-grep)
    [[ $# -eq 2 ]] || usage
    resolve_project "$1"
    (( ${#2} >= 2 && ${#2} <= 200 )) || die "search text must be 2-200 characters"
    code_grep "$2"
    ;;
  git-status)
    [[ $# -eq 1 ]] || usage
    resolve_project "$1"
    git_status
    ;;
  update)
    [[ $# -eq 1 ]] || usage
    resolve_project "$1"
    hold_lock
    git_pull
    compose "$1" pull --ignore-buildable
    compose "$1" up -d --build
    ;;
  restart-docker)
    [[ $# -eq 0 ]] || usage
    hold_lock
    systemctl restart docker.service
    ;;
  ha-states)
    [[ $# -le 1 ]] || usage
    [[ "${1:-}" =~ ^[a-z_]*$ ]] || die "invalid domain"
    ha_states "${1:-}"
    ;;
  ha-state)
    [[ $# -eq 1 ]] || usage
    [[ "$1" =~ ^[a-z_]+\.[a-z0-9_]+$ ]] || die "invalid entity_id"
    ha_state "$1"
    ;;
  ha-automations)
    [[ $# -eq 0 ]] || usage
    ha_automations
    ;;
  ha-log)
    [[ $# -le 1 ]] || usage
    lines="$(line_count "${1:-}")"
    docker logs --timestamps --tail "$lines" "$HA_CONTAINER" 2>&1
    ;;
  ha-logbook)
    [[ $# -le 2 ]] || usage
    hours="${1:-$DEFAULT_LOGBOOK_HOURS}"
    [[ "$hours" =~ ^[0-9]{1,3}$ && "$hours" -ge 1 ]] || die "hours must be a number"
    if (( hours > MAX_LOGBOOK_HOURS )); then
      hours=$MAX_LOGBOOK_HOURS
    fi
    [[ -z "${2:-}" || "$2" =~ ^[a-z_]+\.[a-z0-9_]+$ ]] || die "invalid entity_id"
    ha_logbook "$hours" "${2:-}"
    ;;
  loki-search)
    [[ $# -eq 2 || $# -eq 3 ]] || usage
    require_project_name "$1"
    (( ${#2} >= 1 && ${#2} <= 200 )) || die "search text must be 1-200 characters"
    hours="$(hours_arg "${3:-}")"
    loki_search "$1" "$2" "$hours"
    ;;
  loki-errors)
    [[ $# -le 2 ]] || usage
    project="${1:-}"
    if [[ -n "$project" ]]; then
      require_project_name "$project"
    fi
    hours="$(hours_arg "${2:-}")"
    loki_errors "$project" "$hours"
    ;;
  metrics)
    [[ $# -eq 1 ]] || usage
    (( ${#1} >= 1 && ${#1} <= MAX_QUERY_CHARS )) || die "query must be 1-$MAX_QUERY_CHARS characters"
    prometheus_query "$1"
    ;;
  alerts)
    [[ $# -eq 0 ]] || usage
    prometheus_alerts
    ;;
  ha-light)
    [[ $# -eq 2 ]] || usage
    case "$1" in
      on) service=turn_on ;;
      off) service=turn_off ;;
      toggle) service=toggle ;;
      *) die "light action must be on, off or toggle" ;;
    esac
    [[ "$2" =~ ^light\.[a-z0-9_]+$ ]] || die "only light.* entities can be switched"
    ha_light "$service" "$2"
    ;;
  *)
    usage
    ;;
esac
