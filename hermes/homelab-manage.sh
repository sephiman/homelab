#!/usr/bin/env bash
# The only privileged entry point Hermes has on the host (sudoers allows this file and nothing else).
# Works on any Docker Compose project that has containers on this host (running or stopped); the
# project's folder and compose files come from the labels Compose puts on its containers.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly DEFAULT_LINES=100
readonly MAX_LINES=500
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
  update  <project>            git pull --ff-only (if a git checkout), compose pull, up -d --build
  restart-docker               systemctl restart docker (restarts everything)
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

# Compose files often interpolate ${HOME} for bind mounts, so run them with the owner's HOME, not root's.
compose() {
  local project="$1"
  shift
  local owner_home
  owner_home="$(home_of "$(dir_owner "$PROJECT_DIR")")"
  local file_args=() files file
  IFS=',' read -ra files <<<"$CONFIG_FILES"
  for file in "${files[@]}"; do
    file_args+=(--file "$file")
  done
  (cd "$PROJECT_DIR" && HOME="$owner_home" docker compose --project-name "$project" "${file_args[@]}" "$@")
}

# git runs as the folder's owner so the checkout never gains root-owned files.
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
  *)
    usage
    ;;
esac
