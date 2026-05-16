# Postgres + pgAdmin

PostgreSQL 17 with pgAdmin 4 as the web client.

## Services

| Service  | Container    | Host port | Access                       |
|----------|--------------|-----------|------------------------------|
| postgres | `postgresdb` | —         | only via `all_dockers` net   |
| pgadmin  | `pgadmin`    | `5050`    | http://localhost:5050        |

Postgres **does not publish a port to the host**: access happens over the internal `all_dockers` network. Other containers connect using hostname `postgresdb` on port `5432`.

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

## Quick backup

```bash
docker exec postgresdb pg_dump -U <user> <db> > backup.sql
```
