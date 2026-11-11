# Calibre-Web Automated

[Calibre-Web Automated](https://github.com/crocodilestick/calibre-web-automated) (CWA) is an all-in-one eBook library manager. It combines the clean Calibre-Web interface with the full Calibre CLI backend, automated watch-folder ingestion, on-the-fly `kepubify` conversion, and native over-the-air **Kobo Sync**.

## Service

| Service | Container | Host port | Access |
|---|---|---|---|
| calibre-web | `calibre-web` | `8083` | http://localhost:8083 (LAN) or `https://books.<your-domain>` through Nginx Proxy Manager |

## Storage & layout (main OS disk)

All data lives under `${HOME}/calibre`:

| Host Path | Container Path | Purpose |
|---|---|---|
| `${HOME}/calibre/config` | `/config` | Application settings, `app.db` (users, shelves, Kobo sync tokens), `cwa.db` |
| `${HOME}/calibre/library` | `/calibre-library` | Core Calibre library (`metadata.db` and author/book directories) |
| `${HOME}/calibre/ingest` | `/cwa-book-ingest` | Watch folder: drop raw books here (`.epub`, `.mobi`, `.pdf`, etc.) to auto-process, convert, and import |

Create the host directories before the first `up` so Docker does not create them owned by root:

```bash
mkdir -p "$HOME"/calibre/{config,library,ingest}
```

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details.

## Bring it up

```bash
docker compose up -d
docker compose logs -f
```

## First run & Initial Setup

1. Open http://localhost:8083 in your browser.
2. Log in with the default credentials:
   - **Username:** `admin`
   - **Password:** `admin123`
3. Go to **Admin** -> **Edit Basic Configuration** and immediately change the admin password.
4. Verify the external binaries paths in **Admin** -> **Edit Basic Configuration** -> **External Binaries**:
   - **Path to Kepubify E-Book Converter:** `/usr/bin/kepubify`
   - **Path to Calibre E-Book Converter:** `/usr/bin/ebook-convert`
5. Enable uploads under **Feature Configuration**:
   - Check **Enable Uploads**.

---

## Expose via Nginx Proxy Manager & Kobo Sync

Kobo e-readers communicate with Calibre-Web over HTTPS and require specific proxy headers. Without these, Calibre-Web serves download links as plain `http://`, and Kobo downloads fail.

### 1. Configure Nginx Proxy Manager

In http://localhost:81 -> **Hosts -> Proxy Hosts -> Add Proxy Host**:

| Tab | Field | Value |
|---|---|---|
| Details | Domain Names | `books.example.com` |
| Details | Scheme | `http` |
| Details | Forward Hostname / IP | `calibre-web` |
| Details | Forward Port | `8083` |
| Details | Block Common Exploits | on |
| Details | Websockets Support | on |
| SSL | SSL Certificate | Request a new Let's Encrypt certificate |
| SSL | Force SSL, HTTP/2, HSTS | on |

Go to the **Advanced** tab and paste the following block into **Custom Nginx Configuration**:

```nginx
proxy_set_header Host $http_host;
proxy_set_header X-Forwarded-Host $http_host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Scheme $scheme;

# Large buffer adjustments to prevent Kobo sync dropouts
client_max_body_size 100M;
proxy_busy_buffers_size 1024k;
proxy_buffers 4 512k;
proxy_buffer_size 1024k;
```

Click **Save**.

### 2. Enable Kobo Sync in Calibre-Web

1. Log into Calibre-Web at `https://books.example.com` as admin.
2. Go to **Admin** -> **Edit Basic Configuration** -> **Feature Configuration**:
   - Check **Enable Kobo Sync**.
   - Check **Proxy unknown requests to Kobo Store** (keeps official Kobo purchases syncing).
   - Set **Server External Port (for port forwarded API calls)** to `443`.
   - Click **Save**.
3. Click your username in the navigation bar to open your profile:
   - Check **Convert book to Kepub before syncing** (recommended: faster page turns and reading stats on Kobo).
   - Under **Kobo Sync Token**, click **Create/View** to generate your token URL. It will look like:
     ```
     https://books.example.com/kobo/xxxxxxxxxxxxxxxxxxxxxxxx
     ```
   - Copy this URL.

### 3. Configure Your Kobo Device

1. Connect your Kobo e-reader to your computer via USB and tap **Connect** on the screen.
2. In your computer's file manager, navigate to the Kobo drive and show hidden files (`Ctrl+H` on Linux).
3. Open `.kobo/Kobo/Kobo eReader.conf` in a text editor.
4. Locate the `[OneStoreServices]` section (or add it if missing) and set `api_endpoint` to your token URL:
   ```ini
   [OneStoreServices]
   api_endpoint=https://books.example.com/kobo/xxxxxxxxxxxxxxxxxxxxxxxx
   ```
5. Save the file and safely eject/unmount your Kobo.
6. Connect the Kobo to Wi-Fi and tap the **Sync** icon. Your Calibre-Web library and shelves will synchronize wirelessly.

---

## Ingesting Books

There are two ways to add books:

1. **Web UI:** Click the **Upload** button in the top navigation bar.
2. **Automated Watch Folder:** Drop eBook files (`.epub`, `.mobi`, `.azw3`, `.pdf`, etc.) into `${HOME}/calibre/ingest`. CWA automatically detects, converts, organizes, and moves them into the library, then removes them from `ingest/`.

---

## Backups & Restore

The [`backup`](../backup) stack backs up both `${HOME}/calibre/config` and `${HOME}/calibre/library` together in `calibre.tar.gz`. Transient files in `ingest/` are excluded.

To restore:

```bash
docker compose down                                 # in calibre/
tar xzf calibre.tar.gz -C ${HOME}/calibre
docker compose up -d
```
