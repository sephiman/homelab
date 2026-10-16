# Postgres + pgAdmin

PostgreSQL 18 with pgAdmin 4 as the web client.

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

- `pgdata18` — Docker-managed named volume with Postgres data, mounted at `/var/lib/postgresql` (the image keeps the cluster in `18/docker` inside it).
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

## Vaultwarden database

[`vaultwarden.sql`](./vaultwarden.sql) creates the `vaultwarden` role and database used by the [`vaultwarden`](../vaultwarden) stack. See [vaultwarden/README.md](../vaultwarden/README.md#1-create-the-database) for the one-time setup.

## Quick backup

```bash
docker exec postgresdb pg_dump -U <user> <db> > backup.sql
```

Scheduled full backups (`pg_dumpall` → Google Drive) are handled by the [`backup`](../backup) stack.

## Upgrading from 17 to 18

Postgres 18 cannot open a 17 data directory, and the 18 image mounts its volume at `/var/lib/postgresql` instead of `.../data`. The upgrade runs `pg_upgrade --clone` into a new `pgdata18` volume, so the old `pgdata` volume is left untouched as a rollback. `--clone` uses reflinks (btrfs/XFS); on other filesystems use `--copy` and make sure there is free space for a full copy.

1. Check the old cluster's settings — the new one must match them:

   ```bash
   docker exec postgresdb psql -U <user> -d postgres -Atc "select rolname from pg_roles where oid = 10; show data_checksums; select datcollate from pg_database where datname = 'template1';"
   ```

   The first value is the bootstrap superuser (`<user>` below). If checksums are `on`, drop `--no-data-checksums`; if the locale differs, change `--locale`.

2. Stop Postgres and dry-run the upgrade (the dry run creates the empty 18 cluster; the real run reuses it):

   ```bash
   docker compose stop postgres
   docker run --rm \
     -v postgres_pgdata:/old \
     -v postgres_pgdata18:/var/lib/postgresql \
     -e PGDATAOLD=/old \
     -e POSTGRES_INITDB_ARGS="-U <user> --no-data-checksums --encoding=UTF8 --locale=en_US.utf8" \
     tianon/postgres-upgrade:17-to-18 \
     --check -U <user>
   ```

3. When it reports `*Clusters are compatible*`, run the same command with `--clone` instead of `--check`.

4. Re-add the rule that lets other containers connect. `pg_upgrade` does not carry `pg_hba.conf` over, and without it every app fails with `no pg_hba.conf entry for host`:

   ```bash
   docker run --rm -v postgres_pgdata18:/var/lib/postgresql alpine:3.24 \
     sh -c 'echo "host all all all scram-sha-256" >> /var/lib/postgresql/18/docker/pg_hba.conf'
   ```

5. Start the 18 stack (`docker compose up -d`; the "volume already exists" warning is expected), then rebuild statistics and update extensions:

   ```bash
   docker exec postgresdb vacuumdb -U <user> --all --analyze-in-stages --missing-stats-only
   docker exec postgresdb vacuumdb -U <user> --all --analyze-only
   docker run --rm -v postgres_pgdata18:/n:ro alpine:3.24 cat /n/update_extensions.sql \
     | docker exec -i postgresdb psql -U <user> -d postgres
   ```

6. Rebuild the [`backup`](../backup) image (it ships `postgresql18-client`) and run one backup. Once everything is verified, remove the old volume with `docker volume rm postgres_pgdata`. Do not run the generated `delete_old_cluster.sh`.
