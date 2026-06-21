# Backup

Scheduled backups of the homelab data, uploaded to Google Drive with [`rclone`](https://rclone.org) and pruned automatically.

## What it backs up

| Source                | Included                          |
|-----------------------|-----------------------------------|
| `homeassistant`       | full folder                       |
| `qbittorrent`         | full folder                       |
| `rustdesk`            | full folder                       |
| `jellyfin/config`     | **config only** (no media/cache)  |
| PostgreSQL            | `pg_dumpall` — all databases + roles |

Each run produces a dated folder (`YYYY-MM-DD_HHMMSS/`) with one `.tar.gz` per source, a gzipped SQL dump, and a `SHA256SUMS` file for integrity checks.

Sources are mounted **read-only**; PostgreSQL is dumped over the `all_dockers` network against `postgresdb`, so the `pgdata` volume is never touched.

## Schedule & retention

- Runs daily at **03:05** (`BACKUP_CRON`).
- **Local:** keeps the most recent backup only (`LOCAL_KEEP=1`) under `${HOME}/backup/archives`.
- **Google Drive:** keeps copies for **7 days** (`REMOTE_RETENTION_DAYS`), older ones are deleted automatically.

## Prerequisites

The shared network must exist (see the [root README](../README.md#shared-network-all_dockers)):

```bash
docker network create all_dockers
```

The `postgres` stack should be running so the database can be dumped.

## Variables (.env)

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` — credentials for `pg_dumpall` (same as the `postgres` stack).
- `RCLONE_REMOTE`, `RCLONE_PATH` — rclone remote name and destination folder on the Drive.
- `BACKUP_CRON` — cron schedule.
- `LOCAL_KEEP`, `REMOTE_RETENTION_DAYS` — retention.
- `RUN_ON_START` — run a backup immediately on container start (handy for testing).
- `TZ` — timezone for cron and timestamps.

## One-time Google Drive setup

There are two distinct concepts:

- **Remote** (`RCLONE_REMOTE`, e.g. `gdrive`) — the authorized connection to your Google account. It is **not** a folder; it is created once with `rclone config` and stored as an OAuth token in `rclone.conf`.
- **Path** (`RCLONE_PATH`, e.g. `homelab-backups`) — a folder inside the Drive. You do **not** create it by hand: `rclone` creates it (and a dated subfolder per backup) automatically on the first upload.

### Create the remote (interactive, once)

Build the image and run the interactive config — it writes `rclone.conf` into the host-mounted config folder (`${HOME}/backup/config/rclone`):

```bash
docker compose build
docker compose run --rm backup rclone config
```

Answer the prompts:

| Prompt                          | Answer                                              |
|---------------------------------|-----------------------------------------------------|
| `n/s/q`                         | `n` (New remote)                                    |
| `name>`                         | `gdrive` (must match `RCLONE_REMOTE` in `.env`)     |
| `Storage>`                      | `drive` (Google Drive)                              |
| `client_id> / client_secret>`   | leave empty (press Enter)                           |
| `scope>`                        | `1` (full access) — or `3` for `drive.file`         |
| `service_account_file>`         | leave empty                                         |
| `Edit advanced config?`         | `n`                                                 |
| `Use auto config?`              | `y` on a machine with a browser / `n` if headless   |
| (browser)                       | log in to your Google account and **authorize**     |
| `Configure this as a Shared Drive?` | `n` (unless you back up to a Shared Drive)      |
| `Keep this remote?`             | `y`, then `q` to quit                               |

> **Headless server:** answer `n` to "Use auto config", and rclone prints an `rclone authorize "drive"` command. Run it on a laptop/desktop that has rclone and a browser, complete the login there, and paste the resulting token back into the server prompt.

### Verify it works

```bash
docker compose run --rm backup rclone lsd gdrive:
```

This lists the folders in your Drive. The `homelab-backups` folder will not exist yet — it appears automatically after the first backup runs.

## Bring it up

```bash
docker compose up -d --build
docker compose logs -f          # follow the backup log
```

To run a backup on demand:

```bash
docker compose exec backup /usr/local/bin/backup.sh
```

## Restore

Download a backup folder (or take it from `${HOME}/backup/archives`):

```bash
rclone copy gdrive:homelab-backups/<DATE> ./restore/<DATE>
cd ./restore/<DATE>
sha256sum -c SHA256SUMS        # verify integrity
```

Restore a folder (stop the relevant service first):

```bash
tar xzf homeassistant.tar.gz -C ${HOME}/homeassistant
```

Restore PostgreSQL (this recreates all databases and roles):

```bash
gunzip -c postgres-all.sql.gz | docker exec -i postgresdb psql -U <user>
```
