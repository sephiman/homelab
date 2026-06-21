# Homelab

_A [Sephilabs](https://github.com/sephiman) project._

Collection of self-hosted services orchestrated with Docker Compose. Each folder is an independent stack with its own `docker-compose.yml` and README.

## Structure

- [`postgres/`](./postgres) — PostgreSQL 17 database + pgAdmin.
- [`nginx/`](./nginx) — Nginx Proxy Manager (reverse proxy + Let's Encrypt certificates).
- [`monitoring/`](./monitoring) — Portainer for now. Later: Loki, Prometheus and Grafana.
- [`media/`](./media) — Jellyfin (media server) + qBittorrent (torrent client).
- [`remote/`](./remote) — Self-hosted RustDesk (remote access server).
- [`home-automation/`](./home-automation) — Home Assistant + Zigbee2MQTT + Mosquitto (MQTT broker).
- [`backup/`](./backup) — Scheduled backups of service data + PostgreSQL, uploaded to Google Drive via rclone.

## Shared network (`all_dockers`)

Every stack joins the same external Docker network called **`all_dockers`** (aliased as `my-network` inside each compose file). This lets containers in different stacks talk to each other by container name — e.g. Nginx Proxy Manager can reach `pgadmin` or `jellyfin` directly without publishing their ports to the host.

### Create it once (before bringing any stack up)

```bash
docker network create all_dockers
```

Verify it exists:

```bash
docker network ls | grep all_dockers
```

Expected output:

```
xxxxxxxxxxxx   all_dockers   bridge    local
```

The network is declared as `external: true` in every `docker-compose.yml`, so Compose will **not** create it for you. If you bring a stack up without creating it first, you'll get:

```
network all_dockers declared as external, but could not be found
```

You only need to run `docker network create all_dockers` **once per host**; it persists across reboots and across `docker compose down`.

### Why a single shared network?

- Containers in different composes can reach each other by container name (`postgresdb`, `jellyfin`, …).
- No need to publish ports to the host for service-to-service traffic.
- Nginx Proxy Manager can proxy any container in the homelab without extra wiring.

## Bringing up a stack

From each stack folder:

```bash
docker compose up -d
docker compose logs -f
docker compose down
```

## Environment variables

Stacks that need secrets (users, passwords, emails) read them from a local `.env` file. Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

`.env` files are listed in `.gitignore` and are never committed.

## Recommended startup order

1. Create the shared network (`docker network create all_dockers`).
2. `postgres` — database for any service that needs it.
3. `nginx` — reverse proxy to expose the rest.
4. `monitoring` — observability and Docker management.
5. `media` / `remote` / `home-automation` — application stacks.
6. `backup` — after the data stacks exist, so there is something to back up.

## License

Copyright (C) 2026 Sephilabs (https://github.com/sephiman)

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See the [LICENSE](./LICENSE) file for the full text.
