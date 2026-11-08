# Remote

Remote-access services. For now this folder hosts the self-hosted [RustDesk](https://rustdesk.com/) server — a free alternative to TeamViewer/AnyDesk where the signalling and relay servers run on your own infrastructure instead of the vendor's cloud. It also includes [favonia/cloudflare-ddns](https://github.com/favonia/cloudflare-ddns) to keep your public IP synced with Cloudflare DNS records.

## Services

| Service       | Container       | Host ports                              | Role                                                |
|---------------|-----------------|-----------------------------------------|-----------------------------------------------------|
| rustdesk-hbbs | `rustdesk-hbbs` | `21115/tcp`, `21116/tcp`, `21116/udp`   | *ID server* — client registration and NAT hole punching. |
| rustdesk-hbbr | `rustdesk-hbbr` | `21117/tcp`                             | *Relay server* — fallback when no direct P2P is possible. |
| cloudflare-ddns | `cloudflare-ddns` | —                                   | *DDNS updater* — keeps Cloudflare DNS records in sync with your public IP every 5 minutes. |

RustDesk needs both processes: `hbbs` so clients can find each other and `hbbr` to relay traffic when a direct connection isn't possible.

`cloudflare-ddns` is a lightweight, hardened Go binary that runs independently from Home Assistant or Nginx, so your remote access stays reachable even if those services go down. It updates both `vpn.jjnoe.com` and `rustdesk.jjnoe.com` (or whichever subdomains you configure) whenever your ISP changes your public IP.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## Router port forwarding

For clients outside your LAN to connect, forward these ports to the host:

- `21115/tcp` — hbbs control port.
- `21116/tcp` + `21116/udp` — client registration and heartbeat.
- `21117/tcp` — hbbr relay.

Only needed if you intend to use RustDesk from outside your local network.

## Configure the relay domain (.env)

The `hbbs` command in `docker-compose.yml` reads the relay address from environment variables:

```yaml
command: hbbs -r ${RUSTDESK_RELAY_HOST}:${RUSTDESK_RELAY_PORT}
```

Docker Compose substitutes those tokens at parse time using the `.env` file sitting next to the compose. Copy the template and fill it in with your real public address:

```bash
cp .env.example .env
```

```dotenv
# .env
RUSTDESK_RELAY_HOST=rustdesk.mydomain.com   # or a public IP, or LAN IP for LAN-only use
RUSTDESK_RELAY_PORT=21117                   # must match the hbbr port published below
```

- If you don't have a domain: `RUSTDESK_RELAY_HOST=1.2.3.4`.
- LAN-only setup: `RUSTDESK_RELAY_HOST=192.168.x.x`.
- The `.env` file is in `.gitignore` so the real domain never ends up in the repo.

After editing `.env`, recreate the container so it picks up the new command:

```bash
docker compose up -d
```

Verify it took effect:

```bash
docker inspect rustdesk-hbbs --format '{{.Args}}'
# → [hbbs -r rustdesk.mydomain.com:21117]
```

> **Note:** the `command:` directive is interpolated by Compose at YAML parse time, not by the shell inside the container. That means `${VAR}` is replaced *before* the container starts — no shell expansion happens inside RustDesk. This is why the `.env` file must live in the same folder as `docker-compose.yml`.

## Bind mounts

Each service stores its key and state in its own subfolder under the home directory of the user running Docker:

- `${HOME}/rustdesk/hbbs` → `/root` (hbbs)
- `${HOME}/rustdesk/hbbr` → `/root` (hbbr)

`${HOME}` is resolved by Docker Compose from the shell environment of whoever runs `docker compose up`, so the path is portable across machines/users without editing the compose. On your server it expands to `/home/juanjo/rustdesk/...`.

Create the folders before the first `up` (Docker would create them owned by root otherwise):

```bash
mkdir -p "$HOME/rustdesk/hbbs" "$HOME/rustdesk/hbbr"
```

On first boot `hbbs` generates a key pair (`id_ed25519` / `id_ed25519.pub`). The public key is what you paste into each RustDesk client to validate the server. Back up these directories.

## Network

Joins `all_dockers` for consistency, although as an external-facing service exposed via published ports it doesn't need to sit behind Nginx Proxy Manager.

## Bring it up

```bash
docker compose up -d
```

## Configure the RustDesk client

In each client, *Settings → Network → ID/Relay Server*:

- **ID Server:** the value of `RUSTDESK_RELAY_HOST` from `.env` (your domain or host IP).
- **Relay Server:** leave blank — the value passed to hbbs via `-r` is used automatically.
- **API Server:** leave blank.
- **Key:** contents of `id_ed25519.pub` (no line breaks). Get it with:

  ```bash
  cat "$HOME/rustdesk/hbbs/id_ed25519.pub"
  ```

Once the key is configured, the client will only connect through your server.

## Configure Cloudflare DDNS

`cloudflare-ddns` keeps your DNS records (`vpn.jjnoe.com`, `rustdesk.jjnoe.com`, etc.) pointing at your current public IP. It polls every 5 minutes and only updates Cloudflare when the IP actually changes.

### 1. Create an API token in Cloudflare

1. Go to [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens).
2. Click **Create Token**.
3. Use the **Edit zone DNS** template.
4. Under **Zone Resources**, pick **Include → Specific zone** and select your domain (e.g. `jjnoe.com`).
5. Under **Permissions**, make sure it has **Zone → DNS → Edit** for that zone only.
6. Continue, review, and create. Copy the token — it's shown only once.

### 2. Fill in `.env`

```dotenv
CLOUDFLARE_API_TOKEN=your_real_token
DDNS_DOMAINS=vpn.jjnoe.com,rustdesk.jjnoe.com
DDNS_PROXIED=false
```

- `DDNS_DOMAINS` is a comma-separated list. Add or remove subdomains freely — the container picks them up on restart.
- `DDNS_PROXIED=false` is **required** for non-HTTP traffic (WireGuard, RustDesk, SSH). Cloudflare's orange-cloud proxy only routes HTTP/HTTPS.

### 3. Bring the stack up

```bash
docker compose up -d
docker compose logs -f cloudflare-ddns
```

On first boot it will create the A records (if they don't exist yet) and print a success line for each domain. If you see `permission denied` or `token not authorized`, double-check the token's zone scope — it must be limited to your exact domain, not "All zones".

### Hardening notes

The compose file runs the container with minimal privileges:

- `read_only: true` — the container filesystem is mounted read-only (it has no state to persist).
- `cap_drop: all` — drops every Linux capability.
- `security_opt: no-new-privileges:true` — prevents privilege escalation inside the container.
- `network_mode: host` — lets it detect the host's public IP without needing Docker port mapping.
- `IP6_PROVIDER=none` — skips IPv6 detection (most residential setups don't need it; remove this line if you use native IPv6).
