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
readonly MAX_HISTORY_HOURS_AGO=720
readonly LOKI_URL=http://127.0.0.1:3100
readonly PROMETHEUS_URL=http://127.0.0.1:9090
readonly BACKUP_CONTAINER=backup
readonly VPN_CONTAINER=gluetun
readonly DEFAULT_SEARCH_HOURS=24
readonly MAX_SEARCH_HOURS=720  # Loki keeps 30 days
readonly MAX_QUERY_CHARS=500
readonly ERROR_PATTERN='(?i)(error|exception|fatal|panic|traceback)'
# Tags with a full version (1.37.4, v0.5.0, 5.21.1.1-ls123) are pinned; anything else floats.
readonly PINNED_TAG_PATTERN='^v?[0-9]+\.[0-9]+\.[0-9]+'
readonly MANIFEST_TYPES='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
# Full "Authorization: Bearer …" line, root-only, passed to curl as a file so the token never
# shows up in a process list.
readonly HA_AUTH_HEADER_FILE=/etc/homelab-manage/ha_auth_header
# Attributes that can carry secrets or the home's location never leave the wrapper.
readonly HA_HIDDEN_ATTRIBUTES='token|password|secret|key|picture|latitude|longitude|gps'
# Anyone who knows a webhook id can trigger its automation from outside.
readonly HA_HIDDEN_CONFIG_KEYS="$HA_HIDDEN_ATTRIBUTES|webhook_id"
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
  databases                    which Postgres database each container uses (only the name, from its env)
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
  ha-automation <automation.entity>   one automation's triggers, conditions and actions (automations.yaml)
  ha-history <entity_id> [hours] [ending_hours_ago]   state changes of one entity, sensors included
                               (default 24h ending now, max 168h, ending up to 720h ago; last 500)
  ha-light <on|off|toggle> <light.entity>   switch a light — the only Home Assistant write allowed
  loki-search <project> <text> [hours]   log lines containing text, any container of a project (default 24h, max 720h; newest 500)
  loki-errors [project] [hours]          error/exception/fatal/panic lines, one project or all (same limits)
  metrics <promql>             one Prometheus instant query (max 500 chars)
  alerts                       Prometheus alerts currently pending or firing, with their labels
  backup-status                last backup: files, checksums, log, copies on Google Drive
  vpn-status                   gluetun health and its public IP vs. the host's (torrent leak check)
  media-health                 Sonarr/Radarr/Prowlarr health checks (indexers, download client…)
  media-queue                  Sonarr/Radarr download queue with status and error messages
  media-title <sonarr|radarr> <title>   library entry, missing episodes and grab/import history
  media-releases <radarr> <title> | <sonarr> <title> <season>
                               search indexers now and list releases with why each is rejected
                               (slow; grabs nothing)
  daily-report                 one-page summary: alerts, backup, disk, errors, updates (Ubuntu,
                               git, images), media, HA batteries
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
utc_hours_ago() {
  jq -rn --argjson hours "$1" 'now - $hours * 3600 | strftime("%Y-%m-%dT%H:%M:%SZ")'
}

ha_logbook() {
  local hours="$1" entity="$2" start end query
  start="$(utc_hours_ago "$hours")"
  end="$(utc_hours_ago 0)"
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

# Unlike the logbook, history includes sensors with a unit (temperature, lux, power), which is what
# numeric_state triggers and conditions watch.
ha_history() {
  local entity="$1" hours="$2" ending_hours_ago="$3" start end
  start="$(utc_hours_ago $((hours + ending_hours_ago)))"
  end="$(utc_hours_ago "$ending_hours_ago")"
  ha_get "history/period/$start?filter_entity_id=$entity&end_time=$end&minimal_response&no_attributes" |
    jq -r '(.[0] // [])[] | [.last_changed, .state] | @tsv' | tail -n "$MAX_LINES"
}

ha_config_dir() {
  docker inspect --type container \
    --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}' "$HA_CONTAINER"
}

# Prints the automations.yaml list item whose `id:` is $1 (the file the automation editor writes).
automation_config_block() {
  awk -v id="$1" -v quote="'" '
    function flush() {
      if (found) printf "%s", block
      block = ""
      found = 0
    }
    /^- / { flush() }
    { block = block $0 "\n" }
    match($0, /^(- |  )id:/) {
      value = substr($0, RLENGTH + 1)
      gsub(quote, "", value)
      gsub(/[" \t\r]/, "", value)
      if (value == id) found = 1
    }
    END { flush() }'
}

mask_hidden_config_values() {
  awk -v hidden="$HA_HIDDEN_CONFIG_KEYS" '
    match(tolower($0), "^[ -]*[a-z_]*(" hidden ")[a-z_]*:") { print substr($0, 1, RLENGTH) " <hidden>"; next }
    { print }'
}

ha_automation() {
  local entity="$1" state config_id file block
  state="$(ha_get "states/$entity")"
  jq -r '"\(.entity_id): \(.state), mode \(.attributes.mode // "-"), last triggered \(.attributes.last_triggered // "never")"' <<<"$state"
  config_id="$(jq -r '.attributes.id // empty' <<<"$state")"
  [[ -n "$config_id" ]] || die "$entity has no id: it is defined in YAML outside automations.yaml, which the wrapper doesn't read"
  file="$(ha_config_dir)/automations.yaml"
  [[ -f "$file" ]] || die "automations.yaml not found in the Home Assistant config folder"
  block="$(automation_config_block "$config_id" <"$file")"
  [[ -n "$block" ]] || die "no automation with id '$config_id' in automations.yaml (defined elsewhere?)"
  head -c "$MAX_CODE_BYTES" <<<"$block" | mask_hidden_config_values
}

# Default 24, capped at MAX_LOGBOOK_HOURS.
ha_hours_arg() {
  local hours="${1:-$DEFAULT_LOGBOOK_HOURS}"
  [[ "$hours" =~ ^[0-9]{1,3}$ && "$hours" -ge 1 ]] || die "hours must be a number"
  if (( hours > MAX_LOGBOOK_HOURS )); then
    hours=$MAX_LOGBOOK_HOURS
  fi
  echo "$hours"
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
  start="$(utc_hours_ago "$hours")"
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
    jq -r '.data.alerts[]
      | [.state, .labels.alertname, (.labels.severity // "-"), .activeAt, (.annotations.summary // ""),
         (.labels | del(.alertname, .severity) | to_entries | map("\(.key)=\(.value)") | join(","))]
      | @tsv'
}

container_mount_source() {
  docker inspect --type container \
    --format "{{range .Mounts}}{{if eq .Destination \"$2\"}}{{.Source}}{{end}}{{end}}" "$1"
}

# Prints one variable of a container's env; used for API keys, which never leave the wrapper.
container_env_value() {
  docker inspect --type container "$1" |
    jq -r --arg key "$2" '.[0].Config.Env[] | select(startswith($key + "=")) | ltrimstr($key + "=")'
}

backup_status() {
  local archives latest
  archives="$(container_mount_source "$BACKUP_CONTAINER" /backups)"
  [[ -n "$archives" && -d "$archives" ]] || die "no $BACKUP_CONTAINER container with a /backups folder"
  latest="$(find "$archives" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -n 1)"
  [[ -n "$latest" ]] || die "no local backup in $archives"
  echo "--- latest local backup: $(basename "$latest")"
  ls -lh "$latest" | tail -n +2
  echo "--- checksums"
  if [[ ! -f "$latest/SHA256SUMS" ]]; then
    echo "no SHA256SUMS: this run stopped before finishing (see the log below)"
  else
    (cd "$latest" && timeout 900 sha256sum -c --quiet SHA256SUMS && echo "all files match SHA256SUMS") ||
      echo "CHECKSUM CHECK FAILED (see above)"
  fi
  echo "--- backup log (last run)"
  docker logs --tail 300 "$BACKUP_CONTAINER" 2>&1 |
    grep -E '===|ready|Uploading|ERROR|Error|error|FAILED|failed|denied' | tail -n 25 || true
  echo "--- on Google Drive"
  timeout 120 docker exec "$BACKUP_CONTAINER" sh -c \
    'rclone lsf --dirs-only "$RCLONE_REMOTE:$RCLONE_PATH" && echo "latest:" &&
     rclone size "$RCLONE_REMOTE:$RCLONE_PATH/$1"' sh "$(basename "$latest")" 2>&1 | tail -n 20
}

# qBittorrent shares gluetun's network, so gluetun's public IP is the one torrents use.
vpn_status() {
  local vpn_info host_ip vpn_ip
  docker inspect --type container --format \
    '{{.Name}}: {{.State.Status}}, health {{with index .State "Health"}}{{.Status}}{{else}}none{{end}}' \
    "$VPN_CONTAINER"
  vpn_info="$(docker exec "$VPN_CONTAINER" wget -qO- -T 15 https://ipinfo.io/json 2>&1)" ||
    die "no internet through the VPN: $vpn_info"
  vpn_ip="$(jq -r '.ip' <<<"$vpn_info")"
  host_ip="$(curl -fsS --max-time 15 https://ipinfo.io/ip)" || host_ip=unknown
  jq -r '"VPN exit: \(.ip) \(.city // ""), \(.country // "") (\(.org // ""))"' <<<"$vpn_info"
  if [[ "$vpn_ip" == "$host_ip" ]]; then
    echo "WARNING: the VPN exit IP is the host's own public IP — torrent traffic is not tunnelled"
  else
    echo "leak check: OK (differs from the host's public IP)"
  fi
}

# Sets ARR_URL, ARR_CONTAINER, ARR_KEY_VAR and ARR_API for sonarr, radarr or prowlarr.
resolve_arr() {
  case "$1" in
    sonarr) ARR_URL=http://127.0.0.1:8989 ARR_API=v3 ;;
    radarr) ARR_URL=http://127.0.0.1:7878 ARR_API=v3 ;;
    prowlarr) ARR_URL=http://127.0.0.1:9696 ARR_API=v1 ;;
    *) die "app must be sonarr or radarr" ;;
  esac
  ARR_CONTAINER="$1"
  ARR_KEY_VAR="${1^^}__AUTH__APIKEY"
}

# The key goes in through a file descriptor so it never appears in the process list.
arr_get() {
  local key
  key="$(container_env_value "$ARR_CONTAINER" "$ARR_KEY_VAR")"
  [[ -n "$key" ]] || die "$ARR_CONTAINER has no $ARR_KEY_VAR in its env (media/.env)"
  curl -fsS --max-time "${ARR_TIMEOUT:-20}" -H @<(printf 'X-Api-Key: %s\n' "$key") \
    "$ARR_URL/api/$ARR_API/$1"
}

media_health() {
  local app
  for app in sonarr radarr prowlarr; do
    resolve_arr "$app"
    echo "--- $app"
    arr_get health | jq -r 'if length == 0 then "ok" else .[] | "\(.type): \(.message)" end' ||
      echo "unreachable"
  done
}

media_queue() {
  local app
  for app in sonarr radarr; do
    resolve_arr "$app"
    echo "--- $app"
    arr_get "queue?pageSize=100&includeUnknownSeriesItems=true&includeSeries=true&includeEpisode=true&includeMovie=true" |
      jq -r 'if .totalRecords == 0 then "empty" else .records[]
        | [(.series.title // .movie.title // "?")
           + (if .episode then " S\(.episode.seasonNumber)E\(.episode.episodeNumber)" else "" end),
           .status, (.trackedDownloadState // "-"), (.trackedDownloadStatus // "-"),
           (.timeleft // "-"), .title,
           ([.errorMessage // empty] + [.statusMessages[]?.messages[]?] | join(" | "))]
        | @tsv end'
  done
}

# Prints the library id of the title, preferring an exact (case-insensitive) title match.
arr_find_id() {
  local collection="$1" title="$2" matches
  matches="$(arr_get "$collection" | jq -c --arg title "$title" '
    [.[] | select(.title | ascii_downcase | contains($title | ascii_downcase))]
    | (map(select(.title | ascii_downcase == ($title | ascii_downcase)))) as $exact
    | if ($exact | length) == 1 then $exact else . end')"
  case "$(jq length <<<"$matches")" in
    0) die "no '$title' in the $ARR_CONTAINER library (it was never added, or the title differs)" ;;
    1) jq -r '.[0].id' <<<"$matches" ;;
    *) jq -r '.[] | "  \(.title) (\(.year))"' <<<"$matches" | head -n 15 >&2
       die "several titles match '$title'; use the full title" ;;
  esac
}

quality_profile_name() {
  arr_get qualityprofile | jq -r --argjson id "$1" '.[] | select(.id == $id) | .name'
}

media_title() {
  local app="$1" title="$2" id entry
  resolve_arr "$app"
  if [[ "$app" == radarr ]]; then
    id="$(arr_find_id movie "$title")"
    entry="$(arr_get "movie/$id")"
    jq -r '"\(.title) (\(.year)): monitored \(.monitored), downloaded \(.hasFile), status \(.status), available \(.isAvailable) (minimum availability: \(.minimumAvailability))"' <<<"$entry"
    echo "quality profile: $(quality_profile_name "$(jq '.qualityProfileId' <<<"$entry")")"
    echo "--- history (newest first)"
    arr_get "history/movie?movieId=$id" |
      jq -r 'sort_by(.date) | reverse | .[:30][]
        | [.date, .eventType, .sourceTitle, (.data.message // .data.reason // "")] | @tsv'
  else
    id="$(arr_find_id series "$title")"
    entry="$(arr_get "series/$id")"
    jq -r '"\(.title) (\(.year)): monitored \(.monitored), status \(.status), \(.statistics.episodeFileCount)/\(.statistics.episodeCount) monitored episodes downloaded"' <<<"$entry"
    echo "quality profile: $(quality_profile_name "$(jq '.qualityProfileId' <<<"$entry")")"
    echo "--- aired, monitored, not downloaded"
    arr_get "episode?seriesId=$id" | jq -r --arg now "$(utc_hours_ago 0)" '
      [.[] | select(.monitored and (.hasFile | not) and .airDateUtc != null and .airDateUtc < $now)]
      | if length == 0 then "none" else .[:40][] | "S\(.seasonNumber)E\(.episodeNumber)  aired \(.airDateUtc)  \(.title // "")" end'
    echo "--- history (newest first)"
    arr_get "history/series?seriesId=$id&includeEpisode=true" |
      jq -r 'sort_by(.date) | reverse | .[:30][]
        | [.date, .eventType, (if .episode then "S\(.episode.seasonNumber)E\(.episode.episodeNumber)" else "-" end),
           .sourceTitle, (.data.message // .data.reason // "")] | @tsv'
  fi
}

# An interactive search: the app asks every indexer and explains each rejection, grabbing nothing.
media_releases() {
  local app="$1" title="$2" season="${3:-}" id query
  resolve_arr "$app"
  if [[ "$app" == radarr ]]; then
    id="$(arr_find_id movie "$title")"
    query="release?movieId=$id"
  else
    [[ "$season" =~ ^[0-9]{1,3}$ ]] || die "sonarr needs a season number"
    id="$(arr_find_id series "$title")"
    query="release?seriesId=$id&seasonNumber=$season"
  fi
  ARR_TIMEOUT=180 arr_get "$query" | jq -r '
    if length == 0 then "no releases found on any indexer" else
      "\(length) releases, \(map(select(.approved)) | length) acceptable",
      (sort_by([(.approved | not), -(.seeders // 0)]) | .[:40][]
       | [(if .approved then "OK" else "rejected" end), .quality.quality.name,
          "\((.size / 1073741824 * 10 | round) / 10) GB", "seeders \(.seeders // "-")", .indexer, .title,
          ((.rejections // []) | map(if type == "object" then .reason else . end) | join(" | "))]
       | @tsv)
    end'
}

compose_projects() {
  docker ps --all --format '{{.Label "com.docker.compose.project"}}' | grep -v '^$' | sort -u
}

# Commits upstream that touch the project's folder; `update <project>` pulls and redeploys them.
# Runs in a subshell per project because resolve_project dies on a project whose folder is gone.
pending_git_updates() {
  local project
  for project in $(compose_projects); do
    (
      resolve_project "$project"
      is_git_checkout || exit 0
      owner_git fetch --quiet 2>/dev/null || { echo "$project: git fetch failed"; exit 0; }
      incoming="$(owner_git log --oneline 'HEAD..@{upstream}' -- . 2>/dev/null)" || exit 0
      [[ -n "$incoming" ]] || exit 0
      if owner_git merge-base --is-ancestor HEAD '@{upstream}'; then
        pull="fast-forward"
      else
        pull="DIVERGED, git pull --ff-only will fail"
      fi
      changes="$(owner_git status --porcelain --untracked-files=no | wc -l)"
      echo "$project: $(wc -l <<<"$incoming") commits to pull ($pull, $changes local changes)"
      head -n 5 <<<"$incoming" | sed 's/^/  /'
    ) || true
  done
}

# Digest the registry serves for a tag right now, asked anonymously with HEAD (which doesn't count
# against Docker Hub's pull limit). Prints nothing for private images.
registry_digest() {
  local registry="$1" repository="$2" tag="$3" url challenge realm service token
  url="https://$registry/v2/$repository/manifests/$tag"
  challenge="$(curl -sS --max-time 15 -o /dev/null -D - -I "$url" | tr -d '\r' |
    grep -i '^www-authenticate: bearer')" || return 1
  realm="$(sed -nE 's/.*realm="([^"]+)".*/\1/p' <<<"$challenge")"
  service="$(sed -nE 's/.*service="([^"]+)".*/\1/p' <<<"$challenge")"
  token="$(curl -fsS --max-time 15 -G "$realm" --data-urlencode "service=$service" \
    --data-urlencode "scope=repository:$repository:pull" 2>/dev/null | jq -r '.token // .access_token')" ||
    return 1
  curl -fs --max-time 15 -I -H @<(printf 'Authorization: Bearer %s\n' "$token") \
    -H "Accept: $MANIFEST_TYPES" "$url" |
    tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }'
}

# Containers on a floating tag (none, latest, major-only like postgres:18 or v3) whose tag now
# points to a newer image upstream; `update <project>` pulls and recreates them. Exact versions
# (1.37.4) are someone's deliberate pin, digests and locally built images can't move.
pending_image_updates() {
  local container project image image_id name tag built_by repo_digests registry repository remote checked=0
  while IFS=$'\t' read -r container project image image_id; do
    [[ "$image" != *@* ]] || continue
    name="${image%:*}"
    tag="${image##*:}"
    if [[ "$name" == "$image" || "$tag" == */* ]]; then
      name="$image"
      tag=latest
    fi
    [[ ! "$tag" =~ $PINNED_TAG_PATTERN ]] || continue
    # Compose labels the images it builds; those have nothing to pull. A removed image is skipped.
    IFS='|' read -r built_by repo_digests < <(docker image inspect --format \
      '{{index .Config.Labels "com.docker.compose.project"}}|{{range .RepoDigests}}{{.}} {{end}}' \
      "$image_id" 2>/dev/null) || continue
    [[ -z "$built_by" && -n "$repo_digests" ]] || continue
    if [[ "${name%%/*}" == *[.:]* && "${name%%/*}" != docker.io ]]; then
      registry="${name%%/*}"
      repository="${name#*/}"
    else
      registry=registry-1.docker.io
      repository="${name#docker.io/}"
      [[ "$repository" == */* ]] || repository="library/$repository"
    fi
    checked=$((checked + 1))
    remote="$(registry_digest "$registry" "$repository" "$tag")" || remote=""
    if [[ -z "$remote" ]]; then
      echo "$project/$container ($image): could not check the registry"
    elif ! grep -qF "@$remote" <<<"$repo_digests"; then
      echo "$project/$container ($image): newer image available"
    fi
  done < <(docker ps --all --quiet | xargs --no-run-if-empty docker inspect --type container | jq -r '
    .[]
    | select(.Config.Labels["com.docker.compose.project"])
    | [(.Name | ltrimstr("/")), .Config.Labels["com.docker.compose.project"], .Config.Image, .Image]
    | @tsv')
  echo "($checked containers on floating tags checked)"
}

# Everything the morning summary needs in one call, so the scheduled run uses no tools.
daily_report() {
  echo "=== alerts"
  prometheus_alerts || echo "Prometheus unreachable"
  echo "=== backup"
  prometheus_query 'homelab_backup_last_run_success' || true
  prometheus_query '(time() - homelab_backup_last_success_timestamp_seconds) / 3600' | sed 's/$/ hours since last success/' || true
  echo "=== disk (used %)"
  df -h -x tmpfs -x overlay -x devtmpfs -x squashfs -x efivarfs || true
  echo "=== containers not running or unhealthy"
  docker ps --all --format '{{.Names}} | {{.Status}}' | grep -vE '\| Up [^(]*$|\(healthy\)' || echo "none"
  echo "=== restarts in 24h (by container)"
  prometheus_query 'changes(container_start_time_seconds{name!=""}[24h]) > 0' || true
  echo "=== error lines in 24h (by container)"
  loki_errors "" 24 | awk '{print $2}' | sort | uniq -c | sort -rn | head -n 15 || true
  echo "=== pending Ubuntu updates"
  apt list --upgradable 2>/dev/null | grep -c -- '-security' | sed 's/$/ security/' || true
  apt list --upgradable 2>/dev/null | tail -n +2 | wc -l | sed 's/$/ total/' || true
  echo "=== projects with commits to pull (update <project> pulls and rebuilds)"
  pending_git_updates | grep . || echo "none"
  echo "=== containers on floating tags with a newer image (update <project> pulls and recreates)"
  pending_image_updates || true
  echo "=== media"
  media_health 2>&1 || true
  # Unavailable entities are left out: they come and go with Zigbee/Wi-Fi drops and are noise here.
  echo "=== Home Assistant: low batteries"
  if [[ -r "$HA_AUTH_HEADER_FILE" ]]; then
    ha_get states | jq -r '.[]
      | select(.attributes.device_class == "battery" and (.state | tonumber? // 100) < 20)
      | [.entity_id, .state, (.attributes.friendly_name // "")] | @tsv' | head -n 40
  else
    echo "not configured"
  fi
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

# Database names often differ from project names (curset -> tradeschool) and only the env says so;
# of the env, only a database name ever leaves this function.
container_databases() {
  docker ps --all --quiet | xargs --no-run-if-empty docker inspect --type container | jq -r '
    .[]
    | (.Config.Labels["com.docker.compose.project"] // "-") as $project
    | (.Name | ltrimstr("/")) as $container
    | .Config.Env[]?
    | capture("^(?<key>[A-Z0-9_]+)=(?<value>.*)$")
    | if (.key | test("^(DB_NAME|POSTGRES_DB|PGDATABASE|DATABASE_NAME)$")) then .value
      elif (.key | test("(DATABASE|DB|DATASOURCE)_URL$")) and (.value | test("^(jdbc:)?postgres"))
        then .value | capture("/(?<db>[A-Za-z0-9_-]+)(\\?|$)").db
      else empty end
    | select(test("^[A-Za-z0-9_-]{1,63}$"))
    | [$project, $container, .]
    | @tsv' | sort -u
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
  databases)
    [[ $# -eq 0 ]] || usage
    container_databases
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
    hours="$(ha_hours_arg "${1:-}")"
    [[ -z "${2:-}" || "$2" =~ ^[a-z_]+\.[a-z0-9_]+$ ]] || die "invalid entity_id"
    ha_logbook "$hours" "${2:-}"
    ;;
  ha-automation)
    [[ $# -eq 1 ]] || usage
    [[ "$1" =~ ^automation\.[a-z0-9_]+$ ]] || die "invalid automation entity_id"
    ha_automation "$1"
    ;;
  ha-history)
    [[ $# -ge 1 && $# -le 3 ]] || usage
    [[ "$1" =~ ^[a-z_]+\.[a-z0-9_]+$ ]] || die "invalid entity_id"
    hours="$(ha_hours_arg "${2:-}")"
    ending_hours_ago="${3:-0}"
    [[ "$ending_hours_ago" =~ ^[0-9]{1,3}$ ]] && (( ending_hours_ago <= MAX_HISTORY_HOURS_AGO )) ||
      die "ending_hours_ago must be 0-$MAX_HISTORY_HOURS_AGO"
    ha_history "$1" "$hours" "$ending_hours_ago"
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
  backup-status)
    [[ $# -eq 0 ]] || usage
    backup_status
    ;;
  vpn-status)
    [[ $# -eq 0 ]] || usage
    vpn_status
    ;;
  media-health)
    [[ $# -eq 0 ]] || usage
    media_health
    ;;
  media-queue)
    [[ $# -eq 0 ]] || usage
    media_queue
    ;;
  media-title)
    [[ $# -eq 2 ]] || usage
    [[ "$1" == sonarr || "$1" == radarr ]] || die "app must be sonarr or radarr"
    (( ${#2} >= 2 && ${#2} <= 100 )) || die "title must be 2-100 characters"
    media_title "$1" "$2"
    ;;
  media-releases)
    [[ $# -eq 2 || $# -eq 3 ]] || usage
    [[ "$1" == sonarr || "$1" == radarr ]] || die "app must be sonarr or radarr"
    (( ${#2} >= 2 && ${#2} <= 100 )) || die "title must be 2-100 characters"
    media_releases "$1" "$2" "${3:-}"
    ;;
  daily-report)
    [[ $# -eq 0 ]] || usage
    daily_report
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
