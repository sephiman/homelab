# Remote

Remote-access services. For now this folder hosts the self-hosted [RustDesk](https://rustdesk.com/) server — a free alternative to TeamViewer/AnyDesk where the signalling and relay servers run on your own infrastructure instead of the vendor's cloud.

## Services

| Service       | Container       | Host ports                              | Role                                                |
|---------------|-----------------|-----------------------------------------|-----------------------------------------------------|
| rustdesk-hbbs | `rustdesk-hbbs` | `21115/tcp`, `21116/tcp`, `21116/udp`   | *ID server* — client registration and NAT hole punching. |
| rustdesk-hbbr | `rustdesk-hbbr` | `21117/tcp`                             | *Relay server* — fallback when no direct P2P is possible. |

RustDesk needs both processes: `hbbs` so clients can find each other and `hbbr` to relay traffic when a direct connection isn't possible.

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
