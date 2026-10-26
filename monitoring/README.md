# Monitoring

Homelab management + observability stack (Phase 1: full coverage for the `home-automation` stack).

## Services

| Service              | Container               | Host port      | Purpose                                                                |
|----------------------|-------------------------|----------------|------------------------------------------------------------------------|
| dockhand             | `dockhand`              | `9000`         | Docker UI (containers, stacks, logs, shell)                            |
| grafana              | `grafana`               | `3000`         | Dashboards                                                             |
| prometheus           | `prometheus`            | `9090` (loopback) | Metrics store + alert rules, 15d retention                          |
| alertmanager         | `alertmanager`          | `9093` (loopback) | Routes firing Prometheus alerts to Telegram                         |
| docker-socket-proxy  | `docker-socket-proxy`   | —              | Read-only Docker API for Prometheus service discovery                  |
| loki                 | `loki`                  | `3100` (loopback) | Log store, 30d retention                                            |
| alloy                | `alloy`                 | `12345` (loopback) | Grafana Alloy — ships every container's logs to Loki               |
| node-exporter        | `node-exporter`         | —              | Host CPU/mem/disk/net metrics                                          |
| cadvisor             | `cadvisor`              | —              | Per-container metrics                                                  |
| mosquitto-exporter   | `mosquitto-exporter`    | —              | MQTT broker metrics (clients, messages/sec)                            |

Internal-only services (no host port) are scraped by Prometheus through the shared `all_dockers` network using their container names. Prometheus, Alertmanager, Loki and Alloy bind to `127.0.0.1` — their UIs work from the host (`http://localhost:9090`, …) but are not reachable from the LAN.

## Auto-discovery: how to add a new scrape target

Add two labels to your container and Prometheus picks it up within ~15s — no edits to `prometheus.yml`.

```yaml
services:
  my-app:
    # ...your service definition...
    networks:
      - my-network
    labels:
      prometheus.scrape: "true"
      prometheus.port: "8000"   # the internal port serving /metrics
```

Verify at http://localhost:9090/targets. Each target is labelled with `instance=<container-name>`, so PromQL like `up{instance="my-app"} == 0` works directly.

Constraints:
- The app must serve metrics at `/metrics` (Prometheus default).
- The container must be attached to the `all_dockers` network (it almost certainly already is).

Discovery uses a read-only [Docker socket proxy](https://github.com/Tecnativa/docker-socket-proxy) sidecar — Prometheus never sees `/var/run/docker.sock` directly.

## What this monitors

- **Host**: CPU, memory, disk, network, filesystems (node-exporter).
- **All containers**: CPU/memory/IO/restart counts (cAdvisor).
- **All container logs**: shipped by Alloy via Docker service discovery — labels include `container`, `compose_project`, `compose_service`, `stream`.
- **Home Assistant**: native Prometheus integration (entity states, automations, recorder, system stats).
- **Mosquitto / MQTT**: messages in/out, connected clients, retained messages.
- **Media stack** (`media/`): Sonarr, Radarr and Prowlarr via Exportarr — queues, library size, indexer stats, health issues. Alerts in `prometheus/rules/media.yml`.
- **`satoshi-scanner`** (optional): a personal Python Bitcoin-address scanner in a separate repo ([sephiman/satoshi-scanner](https://github.com/sephiman/satoshi-scanner)). Auto-discovered via the `prometheus.scrape` labels; nothing here depends on it.
- **`crypto-ambush`** (optional): the three ambush trading bots — `cambush_*` metrics auto-discovered like the above, plus read-only Postgres datasources for trading performance (see [crypto-ambush dashboard setup](#crypto-ambush-dashboard-setup)).

## Prerequisites

1. Shared network must exist (`docker network create all_dockers`). See [root README](../README.md#shared-network-all_dockers).
2. Copy `.env.example` → `.env` and set `GF_ADMIN_PASSWORD` (and Telegram credentials when ready for alerts).
3. Enable Home Assistant's Prometheus integration (see below) and drop the long-lived token into `prometheus/ha_token`.

### Enable Home Assistant Prometheus integration

Add to `${HOME}/homeassistant/config/configuration.yaml`:

```yaml
prometheus:
  namespace: hass
```

Restart Home Assistant. Then create a long-lived access token in the HA UI (Profile → Security → Long-Lived Access Tokens) and save it:

```bash
cp monitoring/prometheus/ha_token.example monitoring/prometheus/ha_token
# edit the file and paste the token (no quotes, no trailing newline beyond one)
# Prometheus runs as nobody (65534) — a 600 file makes every HA scrape fail and fires HomeAssistantDown
sudo chown "$USER":65534 monitoring/prometheus/ha_token
chmod 640 monitoring/prometheus/ha_token
```

`monitoring/prometheus/ha_token` is gitignored. Re-run the `chown`/`chmod` whenever the file is recreated.

## Bring it up

```bash
cd monitoring
cp .env.example .env   # edit secrets
docker compose up -d
```

First boot:

- Grafana → http://localhost:3000 (login with `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`).
- Datasources (Prometheus, Loki) are provisioned automatically.
- Dockhand → http://localhost:9000. Authentication is **off** on first launch — enable it right away in *Settings → Authentication* and create the admin user.

## Dashboards

Six dashboards are preprovisioned under the **Homelab** folder (auto-loaded from `grafana/dashboards/`):

| File                          | Dashboard                                                                                       |
|-------------------------------|-------------------------------------------------------------------------------------------------|
| `host-overview.json`          | Host CPU/memory/disk/network/load + filesystem table (node-exporter)                            |
| `containers-overview.json`    | Per-container CPU/memory/network, restart count, snapshot table (cAdvisor)                      |
| `container-logs.json`         | Loki log explorer with container/regex/level filters + error & warning counters                 |
| `mosquitto.json`              | MQTT broker — clients, subscriptions, msg/byte rate, dropped, load avg                          |
| `crypto-ambush.json`          | The three crypto-ambush bots — PnL/win rate/positions from Postgres, ops from `cambush_*`, container health + error logs |
| `satoshi-scanner.json`        | Bitcoin address scanner — scan rate, Blockstream latency/rate-limit, DB hit/miss, Telegram alerts |

> `satoshi-scanner` and the crypto-ambush bots are personal Python services living in separate repos ([satoshi-scanner](https://github.com/sephiman/satoshi-scanner)). This stack only consumes their `/metrics` endpoints via the auto-discovery convention above — if a service isn't running, its dashboard simply shows "No data" and the rest of the stack is unaffected. Treat them as examples of how any future app plugs in.

### crypto-ambush dashboard setup

`crypto-ambush.json` covers the three crypto-ambush bots (`ambush-bingx` — the unified BingX combi bot running 15m+1h, `ambush-bitmart`, `ambush-binance-signals`). Besides Prometheus/Loki it reads the trading tables straight from Postgres — one provisioned datasource per bot database (`ambush-pg` → `crypto_ambush`, `ambush-pg-bitmart` → `crypto_ambush_bitmart`, `ambush-pg-signals` → `crypto_ambush_signals`), all via a SELECT-only role. One-time setup:

1. Create the read-only role (pick a password):
   ```bash
   docker cp ../postgres/grafana_ro.sql postgresdb:/tmp/grafana_ro.sql
   docker exec -it postgresdb psql -U root -d postgres -v ro_password='<password>' -f /tmp/grafana_ro.sql
   ```
2. Put the same password in `monitoring/.env` as `AMBUSH_DB_RO_PASSWORD`, then `docker compose up -d grafana`.

The ops-health row uses the bots' `cambush_*` metrics — they're auto-discovered via the `prometheus.scrape` labels already set in the crypto-ambush compose file (rebuild + restart the bots after pulling that change). If the role/password isn't set up, only the Postgres panels error; metrics/log panels keep working.

Edit them in the UI freely (`allowUiUpdates: true`); to make changes survive a restart, export the JSON and overwrite the file.

### Add more dashboards from grafana.com

If you want deeper community dashboards, import them via *Dashboards → New → Import* with these IDs:

| ID    | Dashboard                  |
|-------|----------------------------|
| 1860  | Node Exporter Full         |
| 14282 | cAdvisor compute resources |
| 15239 | Home Assistant             |
| 11352 | Mosquitto                  |

Drop their JSON into `grafana/dashboards/` to make the ones you keep permanent.

## Alerts → Telegram

Prometheus evaluates the rules in `prometheus/rules/*.yml` and pushes firing alerts to Alertmanager, which sends them (and their resolution) to Telegram. Grafana only displays them (*Alerting → Alert rules → Prometheus-managed*); its own contact points are not used.

1. Create a Telegram bot via `@BotFather`, save the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Get your chat ID from `@userinfobot`, save as `TELEGRAM_ALERT_CHAT_ID`. Send the bot a message first — bots can't start a chat.
3. `docker compose up -d alertmanager` (recreates it with the new values).

Alertmanager can't read env vars in its config, so compose hands both values over as secrets (`/run/secrets/telegram_*`) read by `alertmanager/alertmanager.yml`. Left empty, Alertmanager still runs but every send fails with `Unauthorized` in `docker logs alertmanager`.

Routing lives in `alertmanager/alertmanager.yml`: alerts are grouped by `alertname`, re-sent every 4h while firing. Silence one for maintenance at http://localhost:9093.

Baseline rules in `prometheus/rules/home-automation.yml`:
`HomeAssistantDown`, `MosquittoDown`, `HostHighMemory`, `HostHighDisk`, `ContainerRestartingLoop`.
`prometheus/rules/security.yml` adds `CriticalImageVulnerabilities` and `ImageScansStale`, from
Dockhand's image scans. `prometheus/rules/backup.yml` has `BackupFailed` and `BackupStale`, from
the metrics the backup stack writes for node-exporter's textfile collector
(`${HOME}/backup/metrics`, see [backup/README.md](../backup/README.md#monitoring)).
To add one, drop it in a file there and `docker compose restart prometheus`.

## Persistence

Docker-managed named volumes (survive `docker compose down`):

- `dockhand_data` — Dockhand SQLite DB (settings, users, stacks) and its encryption key.
- `prometheus_data` — TSDB (~hundreds of MB/month).
- `alertmanager_data` — silences and notification state.
- `loki_data` — logs + indexes.
- `alloy_data` — Alloy WAL / positions.
- `grafana_data` — Grafana DB (users, alerts, dashboards-in-DB).

## Security notes

- `alloy`, `cadvisor` and `dockhand` bind-mount `/var/run/docker.sock` — UI/agent access is effectively root on the Docker host.
- Dockhand's `/metrics` is public (`EXPORT_METRICS_PUBLIC=true`) so Prometheus can scrape vulnerability counts without an API token — Community edition tokens carry full access. Anyone who can reach port 9000 sees those counts; the rest of Dockhand stays behind its login.
- Only Grafana should be exposed externally (through NPM, with HTTPS). Prometheus, Alertmanager, Loki and Alloy are already loopback-only; exporters publish no host port at all.
- Pin image tags — Loki/Prom schemas can break on `:latest`. Done for everything, Dockhand included.

