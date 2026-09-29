# Vaultwarden

[Vaultwarden](https://github.com/dani-garcia/vaultwarden) is a lightweight server that speaks the Bitwarden API. The official Bitwarden apps (browser extension, desktop, mobile, CLI) work against it unchanged; you just point them at your own server URL.

## Service

| Service     | Container     | Host port | Access                                                      |
|-------------|---------------|-----------|-------------------------------------------------------------|
| vaultwarden | `vaultwarden` | —         | `https://vault.<your-domain>` through Nginx Proxy Manager   |

No port is published to the host. Nginx Proxy Manager reaches the container over `all_dockers` as `vaultwarden:80`. The web vault and every client **require HTTPS**, because the browser crypto APIs are disabled on plain HTTP, so there is no useful `http://localhost` access.

## How data is stored

| What                                    | Where                                                      | Backed up by                     |
|-----------------------------------------|------------------------------------------------------------|----------------------------------|
| Vault (users, ciphers, orgs, folders…)  | `vaultwarden` database in the shared Postgres (`postgresdb`) | `pg_dumpall` in the `backup` stack |
| Attachments, Sends, `rsa_key.pem`       | `${HOME}/vaultwarden/data`                                  | `vaultwarden.tar.gz` in the `backup` stack |
| Favicons (`icon_cache/`)                | `${HOME}/vaultwarden/data`                                  | not backed up (re-downloaded)    |

Everything in the vault is end-to-end encrypted with your master password before it reaches the server. The database holds only ciphertext. **If you forget your master password, nobody can recover it**, not even the admin panel.

> `rsa_key.pem` signs login tokens. If you lose it, clients are logged out and must log in again, but no vault data is lost.

## Prerequisites

1. The shared network: `docker network create all_dockers` (see the [root README](../README.md#shared-network-all_dockers)).
2. The [`postgres`](../postgres) stack running.
3. The [`nginx`](../nginx) stack running, and a DNS record (e.g. `vault.example.com`) pointing at the host.

## First-time setup

### 1. Create the database

Pick a URL-safe password. It ends up inside `DATABASE_URL`, so avoid `@`, `/`, `:`, `#`:

```bash
openssl rand -hex 32
```

Create the role and database with [`postgres/vaultwarden.sql`](../postgres/vaultwarden.sql):

```bash
docker cp postgres/vaultwarden.sql postgresdb:/tmp/vaultwarden.sql
docker exec -it postgresdb psql -U root -d postgres \
  -v vw_password='<password>' -f /tmp/vaultwarden.sql
```

Vaultwarden creates and migrates its own tables on first start.

### 2. Generate the admin token

The `/admin` panel is protected by a token stored as an Argon2 hash:

```bash
docker run --rm -it vaultwarden/server:1.37.3 /vaultwarden hash
```

Type a long random token twice (store it in your password manager, since it's the admin login). The command prints a line like `ADMIN_TOKEN='$argon2id$v=19$...'`. Paste it into `.env` **with the single quotes**. The hash contains `$`, and without the quotes Compose would try to expand it.

### 3. Configure `.env`

```bash
cp .env.example .env
```

- `VAULTWARDEN_DOMAIN`: full public URL, e.g. `https://vault.example.com` (no trailing slash).
- `VAULTWARDEN_DB_PASSWORD`: the password from step 1. `POSTGRES_HOST` defaults to `postgresdb`.
- `ADMIN_TOKEN`: the hash from step 2.
- `SIGNUPS_ALLOWED`: `false` normally; `true` only while you create your own account (see below).
- `INVITATIONS_ALLOWED`: lets you invite others from the admin panel while signups stay closed.
- `SMTP_*`: optional. `SMTP_FROM` must be a bare address (`vault@example.com`, not `"Name <vault@example.com>"`); put the display name in `SMTP_FROM_NAME`. Leave them **commented out** (not blank) to disable mail, because an empty `SMTP_HOST=` counts as set and stops the server from starting. Mail is needed for email-based 2FA, invitation emails, emergency access and "verify email".
- `TZ`: timezone for logs.

> Settings saved from the admin panel are written to `${HOME}/vaultwarden/data/config.json` and **override** the environment. If a `.env` change seems ignored, check that file (or the admin panel).

### 4. Bring it up

```bash
docker compose up -d
docker compose logs -f     # look for "Rocket has launched from http://0.0.0.0:80"
```

### 5. Proxy it with Nginx Proxy Manager

In http://localhost:81 → **Hosts → Proxy Hosts → Add Proxy Host**:

| Tab     | Field                     | Value                         |
|---------|---------------------------|-------------------------------|
| Details | Domain Names              | `vault.example.com`           |
| Details | Scheme                    | `http`                        |
| Details | Forward Hostname / IP     | `vaultwarden`                 |
| Details | Forward Port              | `80`                          |
| Details | Block Common Exploits     | on                            |
| Details | Websockets Support        | **on** (live sync between clients) |
| SSL     | SSL Certificate           | Request a new Let's Encrypt certificate |
| SSL     | Force SSL, HTTP/2, HSTS   | on                            |

**LAN-only (not exposed to the internet)?** Let's Encrypt's HTTP challenge can't reach the host. Use the **DNS Challenge** option on the SSL tab with your DNS provider's API token instead. You get a valid certificate without opening port 443.

Open `https://vault.example.com`. You should see the Bitwarden web vault login.

### 6. Create your account, then close signups

1. Set `SIGNUPS_ALLOWED=true` in `.env` and run `docker compose up -d`.
2. In the web vault, click **Create account**. Use a **strong master password you have never used anywhere else** (a 5–6 word passphrase is ideal) and write it down somewhere physical.
3. Set `SIGNUPS_ALLOWED=false` again and run `docker compose up -d`.

Anyone else (family, partner) gets an invite from `https://vault.example.com/admin` → **Users → Invite User**. Without SMTP, no email is sent, but the invited address can then register on the web vault even with signups closed.

### 7. Turn on two-factor login

Web vault → **Settings → Security → Two-step login → Authenticator app**. Scan the QR code with an authenticator app **that is not this vault** (otherwise you're locked out if the vault is unreachable), and save the recovery code offline.

## Migrating from Bitwarden (bitwarden.com)

Your master password never leaves your devices, so the server can't migrate data for you. The move is **export from Bitwarden → import into Vaultwarden**, done from the web vaults.

### What moves and what doesn't

| Moves with the export                                      | Does **not** move (handle manually)                              |
|------------------------------------------------------------|-------------------------------------------------------------------|
| Logins, secure notes, cards, identities, SSH keys          | **File attachments**: download and re-upload them              |
| Folders, favourites, custom fields, URIs                   | **Sends**: recreate any still needed                           |
| TOTP secrets stored in items (the "Authenticator key")     | **Trash**: restore anything you want to keep *before* exporting |
| Passkeys stored in items (JSON formats)                    | Your Bitwarden **account** 2FA, emergency contacts, settings   |
| Password history per item (JSON formats)                   | Organization items: exported separately (see step 3)           |

### Step 1: Prepare on bitwarden.com

1. Log in to the Bitwarden web vault (`vault.bitwarden.com` or `vault.bitwarden.eu`).
2. Empty or restore the **Trash** (trashed items are not exported).
3. List the items that have **attachments** (the paperclip icon). Download each attachment to a temporary folder.
4. Optional: note the item count per folder so you can verify the import.

### Step 2: Export your individual vault

**Tools → Export vault**:

- **Export from:** *My vault*
- **File format:** `.json (Encrypted)`
- **Export type:** **Password protected**. Pick a strong, one-off file password.

> Choose **Password protected**, not *Account restricted*. An account-restricted export is encrypted with your bitwarden.com account key, so your new Vaultwarden account can't decrypt it. A password-protected file can be imported by any account that knows the file password.
>
> Plain `.json` also works, but it contains every password in clear text. Use it only if the encrypted import fails, and delete the file right after.

Avoid `.csv`: it drops cards, identities, custom-field types, passkeys and password history.

### Step 3: Organizations (only if you use them)

Each Bitwarden organization is exported separately: **Admin Console → (organization) → Settings → Export vault**, same format and options.

In Vaultwarden, first create an organization (**New organization** in the web vault; it's free, with no seat limits). Then import each export **into that organization** in step 4, selecting it as the import destination. Re-invite the members and re-share the collections.

### Step 4: Import into Vaultwarden

1. Log in at `https://vault.example.com` with your new account.
2. **Tools → Import data**:
   - **Destination:** *My vault* (or the organization from step 3)
   - **Folder:** leave empty to keep the original folder structure
   - **File format:** `Bitwarden (json)`
   - Select the export file → **Import** → enter the file password.
3. Compare the item counts with what you noted in step 1.
4. Re-upload the attachments you downloaded to their items.

### Step 5: Point your apps at your server

On each client, **log out** of bitwarden.com first, then on the login screen:

- **Browser extension / desktop / mobile:** tap the **region selector** ("Logging in on: bitwarden.com") → **Self-hosted** → *Server URL* `https://vault.example.com` → Save. Log in with the new account.
- **CLI:** `bw logout && bw config server https://vault.example.com && bw login`

Then re-enable per-device conveniences: biometric unlock, PIN, autofill settings (mobile: re-select Bitwarden as the autofill service if it asks).

### Step 6: Clean up and cut over

1. **Destroy the export files and downloaded attachments.** They're a copy of your vault:
   ```bash
   shred -u bitwarden_export_*.json
   ```
   Empty the browser's downloads list and the OS trash as well.
2. Keep the bitwarden.com account for a couple of weeks, read-only, as a fallback while you confirm everything works on every device.
3. Make sure a Vaultwarden backup has run and that you can restore it (see below). Once your data lives only on your server, the backup *is* your safety net.
4. Only then delete the bitwarden.com account (**Account settings → Danger zone → Delete account**). If you keep a premium subscription, cancel it first.

## Backups and restore

The [`backup`](../backup) stack already covers Vaultwarden:

- the `vaultwarden` database, inside `postgres-all.sql.gz`;
- `${HOME}/vaultwarden/data`, as `vaultwarden.tar.gz` (without `icon_cache`).

A full restore needs **both**, from the same backup date:

```bash
docker compose down                                   # in vaultwarden/
tar xzf vaultwarden.tar.gz -C ${HOME}/vaultwarden/data
gunzip -c postgres-all.sql.gz | docker exec -i postgresdb psql -U <user>   # restores all DBs
docker compose up -d
```

As an extra, independent safety net, do an occasional **password-protected `.json` export** from the Vaultwarden web vault and store it offline (e.g. an encrypted USB stick). It only needs your master password and the file password to restore, even on bitwarden.com.

## Upgrading

The image is pinned (`vaultwarden/server:1.37.3`) because new versions run database migrations. To upgrade:

1. Read the [release notes](https://github.com/dani-garcia/vaultwarden/releases).
2. Run a backup: `docker compose exec backup /usr/local/bin/backup.sh` (in `backup/`).
3. Bump the tag in `docker-compose.yml` (and in the `hash` command above), then `docker compose pull && docker compose up -d`.

## Security notes

- Keep `SIGNUPS_ALLOWED=false`. A public vault with open signups lets strangers host their data on your server.
- `/admin` is guarded only by `ADMIN_TOKEN`. To reduce exposure further, add a custom location `/admin` in NPM (**Advanced** tab) that allows only LAN IPs:
  ```nginx
  location /admin {
      allow 192.168.0.0/16;
      deny all;
      proxy_pass http://vaultwarden:80;
  }
  ```
- Logs go to the monitoring stack (Loki via Alloy) like every other container. Failed logins appear as `Username or password is incorrect` lines if you want a Grafana alert on them.
