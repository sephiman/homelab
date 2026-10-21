#!/usr/bin/env bash
# Prepares the homelab host for Hermes: user, wrapper, sudoers, egress firewall, systemd units and
# secrets. Idempotent; run as root from the repo checkout. Hermes itself is installed afterwards as
# the hermes user (see README).
set -euo pipefail

readonly HERMES_USER=hermes
readonly HERMES_HOME_DIR=/home/hermes
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HOMELAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

step() { printf '\n==> %s\n' "$*"; }
die() {
  echo "install-host: $*" >&2
  exit 1
}

# Writes stdin to a hermes-owned 0600 file. Goes through a private temp file because `install` from
# /dev/stdin isn't reliable across coreutils implementations (Ubuntu 26.04 ships the Rust uutils).
install_secret() {
  local dest="$1" tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$tmp" "$dest"
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
apt-get install -y --no-install-recommends nftables postgresql-client git curl

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

step "Installing the egress firewall"
install -d -o root -g root -m 0755 /etc/hermes
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
if [[ ! -f "$HERMES_HOME_DIR/.hermes/config.yaml" ]]; then
  install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$SCRIPT_DIR/config.yaml" "$HERMES_HOME_DIR/.hermes/config.yaml"
fi
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
  grep -v '^HERMES_DB_RO_PASSWORD=' "$SCRIPT_DIR/.env" | install_secret "$HERMES_HOME_DIR/.hermes/.env"
  printf '127.0.0.1:5432:*:hermes_ro:%s\n' "$db_password" | install_secret "$HERMES_HOME_DIR/.pgpass"
else
  echo "hermes/.env not found: copy .env.example to .env, fill it in and run this script again."
fi

step "Done"
echo "Next: install Hermes as $HERMES_USER, then: systemctl enable --now hermes-gateway (see README)."
