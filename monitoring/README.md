# Monitoring

Homelab management + observability stack (Phase 1: full coverage for the `home-automation` stack).

## Services

| Service              | Container               | Host port      | Purpose                                                                |
|----------------------|-------------------------|----------------|------------------------------------------------------------------------|
| portainer            | `portainer`             | `9000`, `9443` | Docker UI                                                              |
| grafana              | `grafana`               | `3000`         | Dashboards + unified alerting (Telegram)                               |
| prometheus           | `prometheus`            | `9090`         | Metrics store, 15d retention                                           |
| docker-socket-proxy  | `docker-socket-proxy`   | —              | Read-only Docker API for Prometheus service discovery                  |
| loki                 | `loki`                  | `3100`         | Log store, 30d retention                                               |
| alloy                | `alloy`                 | `12345`        | Grafana Alloy — ships every container's logs to Loki                   |
| node-exporter        | `node-exporter`         | —              | Host CPU/mem/disk/net metrics                                          |
| cadvisor             | `cadvisor`              | —              | Per-container metrics                                                  |
| mosquitto-exporter   | `mosquitto-exporter`    | —              | MQTT broker metrics (clients, messages/sec)                            |

Internal-only services (no host port) are scraped by Prometheus through the shared `all_dockers` network using their container names.

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
- **`crypto-signal-sweep`** (optional): a personal Python trading bot in a separate repo ([sephiman/crypto-signal-sweep](https://github.com/sephiman/crypto-signal-sweep)). The stack picks up its metrics if it's running; nothing here depends on it.
- **`satoshi-scanner`** (optional): a personal Python Bitcoin-address scanner in a separate repo ([sephiman/satoshi-scanner](https://github.com/sephiman/satoshi-scanner)). Auto-discovered the same way as `crypto-signal-sweep`.
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
```

`monitoring/prometheus/ha_token` is gitignored.

## Bring it up

```bash
cd monitoring
cp .env.example .env   # edit secrets
docker compose up -d
```

First boot:

- Grafana → http://localhost:3000 (login with `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`).
- Datasources (Prometheus, Loki) are provisioned automatically.
- Portainer → http://localhost:9000 (create admin user within the first few minutes).

## Dashboards

Seven dashboards are preprovisioned under the **Homelab** folder (auto-loaded from `grafana/dashboards/`):

| File                          | Dashboard                                                                                       |
|-------------------------------|-------------------------------------------------------------------------------------------------|
| `host-overview.json`          | Host CPU/memory/disk/network/load + filesystem table (node-exporter)                            |
| `containers-overview.json`    | Per-container CPU/memory/network, restart count, snapshot table (cAdvisor)                      |
| `container-logs.json`         | Loki log explorer with container/regex/level filters + error & warning counters                 |
| `mosquitto.json`              | MQTT broker — clients, subscriptions, msg/byte rate, dropped, load avg                          |
| `crypto-signal-sweep.json`    | Trading bots — candle freshness, signals, loop health, exchange latency/errors, side errors     |
| `crypto-ambush.json`          | The three crypto-ambush bots — PnL/win rate/positions from Postgres, ops from `cambush_*`, container health + error logs |
| `satoshi-scanner.json`        | Bitcoin address scanner — scan rate, Blockstream latency/rate-limit, DB hit/miss, Telegram alerts |

> `crypto-signal-sweep` and `satoshi-scanner` are personal Python services living in separate repos ([crypto-signal-sweep](https://github.com/sephiman/crypto-signal-sweep), [satoshi-scanner](https://github.com/sephiman/satoshi-scanner)). This stack only consumes their `/metrics` endpoints via the auto-discovery convention above — if a service isn't running, its dashboard simply shows "No data" and the rest of the stack is unaffected. Treat them as examples of how any future app plugs in.

### crypto-ambush dashboard setup

`crypto-ambush.json` covers the three crypto-ambush bots (`ambush-bingx-15m`, `ambush-bingx-1h`, `ambush-binance-signals`). Besides Prometheus/Loki it reads the trading tables straight from Postgres — one provisioned datasource per bot database (`ambush-pg` → `crypto_ambush`, `ambush-pg-1h` → `crypto_ambush_1h`, `ambush-pg-signals` → `crypto_ambush_signals`), all via a SELECT-only role. One-time setup:

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

1. Create a Telegram bot via `@BotFather`, save the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Get your chat ID from `@userinfobot`, save as `TELEGRAM_ALERT_CHAT_ID`.
3. Restart the Grafana container (`docker compose restart grafana`).
4. In Grafana → *Alerting → Contact points → New*, type Telegram. Reference the env vars as `$__env{TELEGRAM_BOT_TOKEN}` and `$__env{TELEGRAM_ALERT_CHAT_ID}`.
5. Set this contact point as the default in *Notification policies*.

Baseline alert rules are pre-loaded from `prometheus/rules/home-automation.yml`:
`HomeAssistantDown`, `MosquittoDown`, `HostHighMemory`, `HostHighDisk`, `ContainerRestartingLoop`.
Grafana will pick them up automatically from the Prometheus datasource (Alerting → Alert rules → Prometheus-managed).

## Persistence

Docker-managed named volumes (survive `docker compose down`):

- `portainer_data` — Portainer config.
- `prometheus_data` — TSDB (~hundreds of MB/month).
- `loki_data` — logs + indexes.
- `alloy_data` — Alloy WAL / positions.
- `grafana_data` — Grafana DB (users, alerts, dashboards-in-DB).

## Security notes

- `alloy`, `cadvisor` and `portainer` bind-mount `/var/run/docker.sock` — UI/agent access is effectively root on the Docker host.
- Only Grafana should be exposed externally (through NPM, with HTTPS). Leave Prom/Loki/Alloy/exporters internal.
- Pin image tags (already done) — Loki/Prom schemas can break on `:latest`.

