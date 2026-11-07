# Backup

Scheduled backups of the homelab data, uploaded to Google Drive with [`rclone`](https://rclone.org) and pruned automatically.

## What it backs up

| Source                | Included                          |
|-----------------------|-----------------------------------|
| `homeassistant`       | full folder; the recorder DB (`home-assistant_v2.db`) as a separate consistent SQLite snapshot |
| `qbittorrent`         | full folder                       |
| `rustdesk`            | full folder                       |
| `jellyfin/config`     | **config only** (no media/cache); no `metadata` (re-downloadable artwork), `log`, `transcodes` |
| `prowlarr`, `sonarr`, `radarr`, `bazarr`, `seerr`, `cleanuparr` config; `maintainerr`, `searcharr` data | config + DB (+ the *arrs' built-in `Backups/`); no `MediaCover`, `logs` |
| `vaultwarden/data`    | attachments, sends, `rsa_key.pem` (no `icon_cache`); the vault itself is in PostgreSQL |
| PostgreSQL            | `pg_dumpall` — all databases + roles |

Each run produces a dated folder (`YYYY-MM-DD_HHMMSS/`) with one `.tar.gz` per source, the Home Assistant database snapshot (`homeassistant-db.sqlite.gz`), a gzipped SQL dump, and a `SHA256SUMS` file for integrity checks.

Live apps keep writing while they are archived. GNU tar then reports `file changed as we read it` (exit 1); the run logs a warning and keeps the archive. Any other tar error fails the run. Home Assistant's recorder DB is the exception: it is excluded from the tarball and copied with `VACUUM INTO` instead, because a tarred live SQLite DB + WAL may not open on restore.

Sources are mounted **read-only**; PostgreSQL is dumped over the `all_dockers` network against `postgresdb`, so the `pgdata18` volume is never touched.

## Schedule & retention

- Runs daily at **03:05** (`BACKUP_CRON`).
- Each source is its own step: one that fails (PostgreSQL down, a `tar` error) is logged and
  skipped, and the rest is still backed up and uploaded. The run then counts as failed.
- Before starting, it checks that the disk has room for the last backup's size plus 2 GB.
- **Upload retries:** a backup folder holds a `.pending-upload` marker until its Google Drive copy
  is uploaded and verified (`rclone check`). `upload.sh` runs right after the backup and then
  hourly (`UPLOAD_CRON`, `:35`), and only sends files Drive doesn't have yet. So a failed or
  interrupted upload is retried within the hour, not the next night. Each attempt is capped at
  `UPLOAD_TIMEOUT` (3h).
- **Local:** keeps the most recent uploaded backup only (`LOCAL_KEEP=1`) under `${HOME}/backup/archives`.
  Backups still waiting for upload are never pruned.
- **Google Drive:** keeps copies for **7 days** (`REMOTE_RETENTION_DAYS`); older ones are deleted
  automatically, but only after an upload run in which every pending backup made it up.

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
- `PG_EXCLUDE_DATABASES` — databases to skip entirely (schema + data), comma/space separated. E.g. `satoshi_scanner`. Empty = dump everything.
- `RCLONE_REMOTE`, `RCLONE_PATH` — rclone remote name and destination folder on the Drive.
- `BACKUP_CRON` — cron schedule.
- `UPLOAD_CRON`, `UPLOAD_TIMEOUT` — retry schedule for uploads that didn't finish, and the cap per attempt.
- `LOCAL_KEEP`, `REMOTE_RETENTION_DAYS` — retention.
- `RUN_ON_START` — run a backup immediately on container start (handy for testing).
- `TZ` — timezone for cron and timestamps.

## One-time Google Drive setup

There are two distinct concepts:

- **Remote** (`RCLONE_REMOTE`, e.g. `gdrive`) — the authorized connection to your Google account. It is **not** a folder; it is created once with `rclone config` and stored as an OAuth token in `rclone.conf`.
- **Path** (`RCLONE_PATH`, e.g. `homelab-backups`) — a folder inside the Drive. You do **not** create it by hand: `rclone` creates it (and a dated subfolder per backup) automatically on the first upload.

### Create your own OAuth client first

Left empty, `client_id` falls back to rclone's built-in client (Google project `202264815644`), whose
per-minute quota is shared by every rclone user worldwide. Uploads then fail at random with
`Error 403: Quota exceeded for quota metric 'Queries' ... consumer 'project_number:202264815644'`.
Create a client of your own, following [rclone's guide](https://rclone.org/drive/#making-your-own-client-id):

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project and enable the **Google Drive API**.
2. Configure the OAuth consent screen (External, add yourself as a test user) and **publish** it — tokens of an app left in *Testing* expire after 7 days.
3. Create an **OAuth client ID** of type *Desktop app* and copy the client ID and secret.

An existing remote can be switched to it without recreating it:

```bash
docker compose run --rm backup rclone config update gdrive client_id <ID> client_secret <SECRET>
docker compose run --rm backup rclone config reconnect gdrive:
```

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
| `client_id> / client_secret>`   | your own OAuth client (see above) — **don't** leave empty |
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

To retry pending uploads now instead of waiting for the hourly run:

```bash
docker compose exec backup /usr/local/bin/upload.sh
```

Both take a lock in `/backups`, so a second copy started meanwhile just logs that it's skipping.

## Monitoring

Every run, successful or not, writes its result to `${HOME}/backup/metrics`, which the
monitoring stack's node-exporter reads (textfile collector). `backup.prom` covers the local backup
(`backup.sh`):

| Metric | Meaning |
|--------|---------|
| `homelab_backup_last_run_timestamp_seconds` | when the last run ended |
| `homelab_backup_last_run_success` | `1` if every step succeeded, `0` if any failed |
| `homelab_backup_last_success_timestamp_seconds` | when the last fully successful run ended |
| `homelab_backup_size_bytes` | size of the last successful backup |

`offsite.prom` covers the Google Drive copy (`upload.sh`, hourly):

| Metric | Meaning |
|--------|---------|
| `homelab_backup_offsite_last_run_timestamp_seconds` | when the last upload run ended |
| `homelab_backup_offsite_last_run_success` | `1` if everything pending was uploaded and Drive was pruned |
| `homelab_backup_offsite_last_upload_timestamp_seconds` | when a backup was last verified on Drive |
| `homelab_backup_offsite_pending` | local backups not yet on Drive |

`monitoring/prometheus/rules/backup.yml` turns them into `BackupFailed` and `BackupStale` (no
local success for 26 h — also what you get when this container is stopped), both critical, and
`BackupOffsiteFailing` (warning: uploads failing for 3 h). Until the first run after deploying,
the metrics don't exist and no alert can fire. Hermes reads all of this with
`backup-status`.

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

Home Assistant's database is not in the tarball; put the snapshot back next to its config:

```bash
gunzip -c homeassistant-db.sqlite.gz > ${HOME}/homeassistant/config/home-assistant_v2.db
```

Restore PostgreSQL (this recreates all databases and roles):

```bash
gunzip -c postgres-all.sql.gz | docker exec -i postgresdb psql -U <user>
```
