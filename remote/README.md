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

## Configure the relay domain

The compose ships with `command: hbbs -r rustdesk.example.com:21117`. You must **replace `rustdesk.example.com` with the domain (or public IP) pointing at the host**. That's the address RustDesk clients will announce as relay.

Options:

- Edit the `command` directly.
- If you don't have a domain, use the public IP: `-r 1.2.3.4:21117`.
- If you'll only use it on the LAN, the server's local IP is enough.

After changing it, `docker compose up -d` recreates the container with the new config.

## Bind mounts

Each service stores its key and state in its own subfolder on the host:

- `/home/juanjo/docker/rustdesk/hbbs` → `/root` (hbbs)
- `/home/juanjo/docker/rustdesk/hbbr` → `/root` (hbbr)

On first boot `hbbs` generates a key pair (`id_ed25519` / `id_ed25519.pub`). The public key is what you paste into each RustDesk client to validate the server. Back up these directories.

## Network

Joins `all_dockers` for consistency, although as an external-facing service exposed via published ports it doesn't need to sit behind Nginx Proxy Manager.

## Bring it up

```bash
docker compose up -d
```

## Configure the RustDesk client

In each client, *Settings → Network → ID/Relay Server*:

- **ID Server:** `rustdesk.example.com` (or the host IP).
- **Relay Server:** leave blank — the value passed to hbbs via `-r` is used automatically.
- **API Server:** leave blank.
- **Key:** contents of `id_ed25519.pub` (no line breaks). Get it with:

  ```bash
  cat /home/juanjo/docker/rustdesk/hbbs/id_ed25519.pub
  ```

Once the key is configured, the client will only connect through your server.
