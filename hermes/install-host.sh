#!/usr/bin/env bash
# Prepares the homelab host for Hermes: user, wrapper, sudoers, egress firewall, systemd units and
# secrets. Idempotent; run as root from the repo checkout. Hermes itself is installed afterwards as
# the hermes user (see README).
set -euo pipefail

readonly HERMES_USER=hermes
readonly HERMES_HOME_DIR=/home/hermes
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HOMELAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# hermes/.env holds one generic LLM_API_KEY; Hermes reads it from the provider's own variable.
declare -rA PROVIDER_KEY_VARS=(
  [opencode-zen]=OPENCODE_ZEN_API_KEY
  [opencode-go]=OPENCODE_GO_API_KEY
  [gemini]=GEMINI_API_KEY
  [openrouter]=OPENROUTER_API_KEY
  [anthropic]=ANTHROPIC_API_KEY
  [openai-api]=OPENAI_API_KEY
  [deepseek]=DEEPSEEK_API_KEY
  [custom:groq]=GROQ_API_KEY
)

step() { printf '\n==> %s\n' "$*"; }
die() {
  echo "install-host: $*" >&2
  exit 1
}

# Writes stdin to a hermes-owned 0600 file. Goes through a private temp file because `install` from
# /dev/stdin isn't reliable across coreutils implementations.
install_secret() {
  local dest="$1" tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$tmp" "$dest"
  rm -f "$tmp"
}

# First `provider:` inside a top-level section of hermes/config.yaml (pinned as the managed config):
# `model` is the primary, `fallback_providers` the first fallback.
configured_provider() {
  local section="$1"
  sed -n "/^$section:/,/^[^ #]/s/^ *-\{0,1\} *provider: *[\"']\{0,1\}\([a-z0-9:-]*\).*/\1/p" \
    "$SCRIPT_DIR/config.yaml" | head -n 1
}

# Prints PROVIDER_VAR=<value of generic_var> for the provider of a config section.
provider_key_line() {
  local generic_var="$1" section="$2" value provider key_var
  value="$(sed -n "s/^$generic_var=//p" "$SCRIPT_DIR/.env")"
  [[ -n "$value" ]] || return 0
  provider="$(configured_provider "$section")"
  [[ -n "$provider" ]] || die "$generic_var is set but config.yaml has no $section provider"
  key_var="${PROVIDER_KEY_VARS[$provider]:-}"
  [[ -n "$key_var" ]] || die "provider '$provider' has no $generic_var mapping: put its own key variable in hermes/.env (see the Hermes provider docs)"
  echo "$generic_var -> $key_var (provider $provider)" >&2
  printf '%s=%s\n' "$key_var" "$value"
}

# Prints the .env Hermes gets: hermes/.env minus the DB password, with the generic keys renamed.
hermes_env() {
  grep -v -e '^HERMES_DB_RO_PASSWORD=' -e '^LLM_API_KEY=' -e '^FALLBACK_API_KEY=' -e '^HA_TOKEN=' \
    "$SCRIPT_DIR/.env" || true
  provider_key_line LLM_API_KEY model
  provider_key_line FALLBACK_API_KEY fallback_providers
}

# HA_TOKEN goes root-only to the wrapper, never to Hermes' .env: a Home Assistant token can also
# switch devices, and the wrapper only ever reads with it.
install_ha_token() {
  local token
  token="$(sed -n 's/^HA_TOKEN=//p' "$SCRIPT_DIR/.env")"
  if [[ -z "$token" || "$token" == changeme ]]; then
    echo "HA_TOKEN not set: Home Assistant actions stay disabled"
    return 0
  fi
  install -d -o root -g root -m 0700 /etc/homelab-manage
  local tmp
  tmp="$(mktemp)"
  printf 'Authorization: Bearer %s\n' "$token" >"$tmp"
  install -o root -g root -m 0600 "$tmp" /etc/homelab-manage/ha_auth_header
  rm -f "$tmp"
}

[[ $EUID -eq 0 ]] || die "run as root: sudo $0"

project_dirs() {
  {
    echo "$HOMELAB_DIR"
    docker ps --all --format '{{.Label "com.docker.compose.project.working_dir"}}'
  } | grep -v '^$' | sort -u
}

owner_homes() {
  local dir
  project_dirs | while read -r dir; do
    if [[ -d "$dir" ]]; then
      getent passwd "$(stat -c %U "$dir")" | cut -d: -f6
    fi
  done | sort -u
}

step "Checking that the homes of the project owners are closed to other users"
# Bind-mounted data (vaultwarden, homeassistant, rclone config, bot data) lives under those homes.
while read -r home; do
  mode="$(stat -c %a "$home")"
  if [[ "${mode: -1}" != 0 ]]; then
    die "$home is accessible to other users; run: chmod o-rwx $home"
  fi
done < <(owner_homes)

step "Installing packages"
apt-get install -y --no-install-recommends nftables postgresql-client git curl jq

step "Creating user $HERMES_USER"
if ! id "$HERMES_USER" &>/dev/null; then
  useradd --create-home --home-dir "$HERMES_HOME_DIR" --shell /bin/bash --user-group "$HERMES_USER"
fi
chmod 700 "$HERMES_HOME_DIR"
for group in docker sudo adm systemd-journal lxd; do
  if id -nG "$HERMES_USER" | tr ' ' '\n' | grep -qx "$group"; then
    die "$HERMES_USER must not be in group '$group' (gpasswd -d $HERMES_USER $group)"
  fi
done

step "Locking down .env files of every compose project"
while read -r dir; do
  if [[ -d "$dir" ]]; then
    find "$dir" -maxdepth 2 -name '.env*' ! -name '*.example' -type f -exec chmod 600 {} +
  fi
done < <(project_dirs)
if [[ -f "$HOMELAB_DIR/monitoring/prometheus/ha_token" ]]; then
  chmod 600 "$HOMELAB_DIR/monitoring/prometheus/ha_token"
fi

step "Installing /usr/local/bin/homelab-manage.sh"
install -o root -g root -m 0755 "$SCRIPT_DIR/homelab-manage.sh" /usr/local/bin/homelab-manage.sh

step "Installing /etc/sudoers.d/hermes"
visudo -cf "$SCRIPT_DIR/hermes.sudoers"
install -o root -g root -m 0440 "$SCRIPT_DIR/hermes.sudoers" /etc/sudoers.d/hermes

step "Installing the managed config and the egress firewall"
install -d -o root -g root -m 0755 /etc/hermes
# Hermes' managed scope: every key here wins over ~hermes/.hermes/config.yaml, and root ownership
# means neither the agent nor `hermes config set` can change it.
install -o root -g root -m 0644 "$SCRIPT_DIR/config.yaml" /etc/hermes/config.yaml
install -o root -g root -m 0644 "$SCRIPT_DIR/hermes-egress.nft" /etc/hermes/hermes-egress.nft
nft -c -f /etc/hermes/hermes-egress.nft
install -o root -g root -m 0644 "$SCRIPT_DIR/hermes-egress.service" /etc/systemd/system/hermes-egress.service
install -o root -g root -m 0644 "$SCRIPT_DIR/hermes-gateway.service" /etc/systemd/system/hermes-gateway.service
systemctl daemon-reload
systemctl enable hermes-egress.service
systemctl restart hermes-egress.service

step "Preparing $HERMES_HOME_DIR"
install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$HERMES_HOME_DIR/workspace" "$HERMES_HOME_DIR/.hermes"
install -o "$HERMES_USER" -g "$HERMES_USER" -m 0644 "$SCRIPT_DIR/AGENTS.md" "$HERMES_HOME_DIR/workspace/AGENTS.md"
# Pre-run scripts of the scheduled jobs (README "Scheduled jobs"); root-owned so the agent can't edit them.
install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$HERMES_HOME_DIR/.hermes/scripts"
install -o root -g root -m 0755 "$SCRIPT_DIR"/scripts/*.sh "$HERMES_HOME_DIR/.hermes/scripts/"
install_secret "$HERMES_HOME_DIR/.pg_service.conf" <<'EOF'
[homelab]
host=127.0.0.1
port=5432
user=hermes_ro
dbname=postgres
EOF

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  step "Installing secrets from hermes/.env"
  db_password="$(sed -n 's/^HERMES_DB_RO_PASSWORD=//p' "$SCRIPT_DIR/.env")"
  [[ -n "$db_password" && "$db_password" != changeme ]] || die "set HERMES_DB_RO_PASSWORD in hermes/.env"
  # Built first so an unmapped provider stops the script before the old .env is replaced.
  env_content="$(hermes_env)"
  printf '%s\n' "$env_content" | install_secret "$HERMES_HOME_DIR/.hermes/.env"
  printf '127.0.0.1:5432:*:hermes_ro:%s\n' "$db_password" | install_secret "$HERMES_HOME_DIR/.pgpass"
  install_ha_token
else
  echo "hermes/.env not found: copy .env.example to .env, fill it in and run this script again."
fi

step "Done"
echo "Next: install Hermes as $HERMES_USER, then: systemctl enable --now hermes-gateway (see README)."
