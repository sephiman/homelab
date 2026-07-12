# Postgres + pgAdmin

PostgreSQL 17 with pgAdmin 4 as the web client.

## Services

| Service  | Container    | Host port                        | Access                                                    |
|----------|--------------|----------------------------------|-----------------------------------------------------------|
| postgres | `postgresdb` | `127.0.0.1:5432` (loopback only) | containers via `all_dockers`; host via `localhost:5432`   |
| pgadmin  | `pgadmin`    | `5050`                           | http://localhost:5050                                     |

Postgres port `5432` is bound to `127.0.0.1` only, so it is reachable from the host itself (psql, IDE database tools) but **not** from the LAN. Other containers connect over the internal `all_dockers` network using hostname `postgresdb` on port `5432`.

## Prerequisite

The shared network must exist before bringing the stack up. From any folder:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. You only need to run this once per host.

## Persistence

- `pgdata` — Docker-managed named volume with Postgres data (`/var/lib/postgresql/data`).
- `pgadmin_data` — Docker-managed named volume with pgAdmin configuration.

## Variables (.env)

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — initial credentials and database.
- `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` — pgAdmin UI login.

## Bring it up

```bash
docker compose up -d
```

## Connect from pgAdmin

In http://localhost:5050, register a new server:

- **Host:** `postgresdb`
- **Port:** `5432`
- **Username / Password:** the ones from `.env`

## Connect from the host

```bash
psql -h 127.0.0.1 -p 5432 -U <user> <db>
```

Or point any GUI client (DataGrip, DBeaver, …) at `localhost:5432`.

## Read-only Grafana role

[`grafana_ro.sql`](./grafana_ro.sql) creates the SELECT-only `grafana_ro` role used by the crypto-ambush dashboards in the `monitoring` stack. See [monitoring/README.md](../monitoring/README.md#crypto-ambush-dashboard-setup) for the one-time setup.

## Quick backup

```bash
docker exec postgresdb pg_dump -U <user> <db> > backup.sql
```

Scheduled full backups (`pg_dumpall` → Google Drive) are handled by the [`backup`](../backup) stack.
