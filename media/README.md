# Media

Homelab media stack. Flow:

```
Seerr / Searcharr (requests) ─▶ Sonarr / Radarr ─▶ Prowlarr (indexers)
                                     │
                                     ▼
              qBittorrent ── tunnelled through gluetun (Surfshark VPN)
                                     │  downloads/
                                     ▼
              Sonarr / Radarr hardlink into series/ movies/ ─▶ Jellyfin
                                     ▲
              Bazarr (subtitles) · Recyclarr (quality profiles) · Cleanuparr / Maintainerr (cleanup)
```

| Service | What it does |
|---------|--------------|
| [Jellyfin](https://jellyfin.org/) | Media server. |
| [gluetun](https://github.com/qdm12/gluetun) | VPN client (Surfshark WireGuard) with a built-in kill switch. qBittorrent's only way out. |
| [qBittorrent](https://www.qbittorrent.org/) ([linuxserver.io](https://docs.linuxserver.io/images/docker-qbittorrent/)) | Torrent client, inside gluetun's network. |
| [Prowlarr](https://prowlarr.com/) | Indexer manager: configure trackers once, synced to Sonarr/Radarr. |
| [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) | Headless browser that solves Cloudflare challenges for Prowlarr (e.g. 1337x). |
| [Sonarr](https://sonarr.tv/) / [Radarr](https://radarr.video/) | Series / movie automation: search, download, rename, import. |
| [Bazarr](https://www.bazarr.media/) | Downloads subtitles for everything Sonarr/Radarr import. |
| [Recyclarr](https://recyclarr.dev/) | Syncs [TRaSH-Guides](https://trash-guides.info/) quality profiles into Sonarr/Radarr daily. No UI. |
| [Seerr](https://github.com/seerr-team/seerr) | Request/discovery UI with Jellyfin login (successor of Jellyseerr/Overseerr). |
| [Searcharr](https://github.com/toddrob99/searcharr) | Telegram bot to add series/movies. |
| [Cleanuparr](https://github.com/Cleanuparr/Cleanuparr) | Removes stalled, slow and malicious downloads; cleans up finished seeds. |
| [Maintainerr](https://github.com/Maintainerr/Maintainerr) | Rule-based deletion of watched/stale media. |
| [Exportarr](https://github.com/onedr0p/exportarr) | Prometheus metrics for Sonarr, Radarr and Prowlarr (see [monitoring](../monitoring/README.md)). |

## Services

| Service | Container | Host port | Reached by other containers as |
|---------|-----------|-----------|--------------------------------|
| jellyfin | `jellyfin` | `8096` | `http://jellyfin:8096` |
| qbittorrent (via gluetun) | `qbittorrent` / `gluetun` | `8881` | `http://gluetun:8881` |
| prowlarr | `prowlarr` | `9696` | `http://prowlarr:9696` |
| flaresolverr | `flaresolverr` | — (not published: unauthenticated proxy) | `http://flaresolverr:8191` |
| sonarr | `sonarr` | `8989` | `http://sonarr:8989` |
| radarr | `radarr` | `7878` | `http://radarr:7878` |
| bazarr | `bazarr` | `6767` | `http://bazarr:6767` |
| seerr | `seerr` | `5055` | `http://seerr:5055` |
| cleanuparr | `cleanuparr` | `11011` | — |
| maintainerr | `maintainerr` | `6246` | — |
| recyclarr, searcharr | — | — | no UI |
| exportarr-{sonarr,radarr,prowlarr} | — | — | `:9707/metrics`, scraped by Prometheus |

qBittorrent runs with `network_mode: service:gluetun`, so it has **no network of its own**: its UI is published on the gluetun container, and every app connects to it as host **`gluetun`**, port `8881` — never `qbittorrent`.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## VPN (Surfshark through gluetun)

Only qBittorrent goes through the VPN — it's the only container that talks to peers. Indexer searches (Prowlarr) and metadata go out normally.

- **Kill switch:** gluetun's firewall blocks everything outside the tunnel, and qBittorrent shares gluetun's network stack. If the VPN drops, qBittorrent simply has no connectivity; your home IP never leaks.
- **No port forwarding:** Surfshark doesn't offer it, so peers can't connect *to* you. Downloads still work (you connect out to peers), but well-seeded torrents are fast while rare ones may be slow, and seeding ratio grows slowly. That's why `6881` is no longer published — nothing could reach it. Fine for public trackers; if you join a ratio-based private tracker, a provider with port forwarding (ProtonVPN, AirVPN, PIA) works with gluetun too.
- **Restart order:** if gluetun is recreated, qBittorrent loses its network. `docker compose up -d` restarts it automatically (`depends_on … restart: true`); after a manual `docker restart gluetun`, also run `docker restart qbittorrent`.

Check the tunnel is up and qBittorrent's public IP is Surfshark's, not yours:

```bash
docker logs gluetun 2>&1 | grep -i "public ip"
docker run --rm --network container:gluetun alpine wget -qO- https://ipinfo.io   # shares qBittorrent's network
```

## Storage layout — one disk, one `/data` root

Everything lives on the `/mnt/media` disk:

```
/mnt/media/
├── movies/             # Radarr root folder, Jellyfin library
├── series/             # Sonarr root folder, Jellyfin library
├── music/
└── downloads/          # qBittorrent save path
    ├── movies/         # category: radarr
    └── series/         # category: sonarr
```

qBittorrent, Sonarr, Radarr, Bazarr and Cleanuparr all mount the **whole** disk at the **same** path, `/mnt/media` → `/data`. That is what makes imports [hardlinks](https://trash-guides.info/File-and-Folder-Structure/Hardlinks-and-Instant-Moves/) instead of copies: the file appears in `movies/` instantly, takes no extra space, and qBittorrent keeps seeding the original in `downloads/`. Don't give them separate mounts for downloads and library — a hardlink can't cross mount points, so every import would turn into a full copy.

Jellyfin keeps mounting `/mnt/media` → `/media`, so its library paths (`/media/movies`, `/media/series`) don't change.

## Bind mounts

Service config lives under the home directory of whoever runs Docker (`${HOME}` is interpolated by Compose at parse time).

| Service | Host | Container |
|---------|------|-----------|
| jellyfin | `${HOME}/jellyfin/config`, `${HOME}/jellyfin/cache` (safe to delete) | `/config`, `/cache` |
| qbittorrent, prowlarr, sonarr, radarr, bazarr, cleanuparr | `${HOME}/<service>/config` | `/config` |
| recyclarr | `${HOME}/recyclarr/config` + [`recyclarr/recyclarr.yml`](./recyclarr/recyclarr.yml) (in this repo, read-only) | `/config` |
| seerr | `${HOME}/seerr/config` | `/app/config` |
| maintainerr | `${HOME}/maintainerr/data` | `/opt/data` |
| searcharr | `${HOME}/searcharr/data` (holds `settings.py`), `${HOME}/searcharr/logs` | `/app/data`, `/app/logs` |
| jellyfin | `/mnt/media` | `/media` |
| qbittorrent, sonarr, radarr, bazarr, cleanuparr | `/mnt/media` | `/data` |

Create the host folders before the first `up` so Docker doesn't create them owned by root:

```bash
mkdir -p "$HOME"/{jellyfin/config,jellyfin/cache,qbittorrent/config,prowlarr/config,sonarr/config,radarr/config,bazarr/config,recyclarr/config,seerr/config,cleanuparr/config,maintainerr/data,searcharr/data,searcharr/logs}
sudo mkdir -p /mnt/media/{movies,series,music,downloads/movies,downloads/series}
sudo chown -R 1000:1000 /mnt/media
```

If a container crash-loops with `EACCES: permission denied` (Seerr, Maintainerr and Recyclarr run as UID 1000 and are the first to hit it), its folder was created by Docker as root because it didn't exist yet. Hand it back and restart:

```bash
sudo chown -R 1000:1000 ~/{prowlarr,sonarr,radarr,bazarr,recyclarr,seerr,cleanuparr} ~/maintainerr ~/searcharr
docker compose up -d
```

## Environment variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

- `SURFSHARK_WIREGUARD_PRIVATE_KEY`, `SURFSHARK_WIREGUARD_ADDRESSES` — from Surfshark → *VPN → Manual setup → WireGuard* → "I don't have a key pair" → generate. The address is the `Address` line of any downloaded WireGuard config (IPv4 only); it's the same for every server.
- `VPN_SERVER_COUNTRIES` — Surfshark countries to connect through (default `Netherlands`).
- `SONARR_API_KEY`, `RADARR_API_KEY`, `PROWLARR_API_KEY` — you pick them (`openssl rand -hex 16` each). Passed to the apps as `<APP>__AUTH__APIKEY`, which overrides the key in their `config.xml`, so Recyclarr and Exportarr work from the first start. Use these same values wherever an API key is asked for below.

The linuxserver images also take `PUID`/`PGID` (`1000`, must be the same on every app that touches `/data`, or imports fail with permission errors) and `TZ` (`Europe/Amsterdam`). Recyclarr and Maintainerr use `user: 1000:1000` instead.

## Bring it up

```bash
docker compose up -d
```

## Migrating from the old `/downloads` mount

qBittorrent used to mount only `/mnt/media/downloads` → `/downloads`; now it sees the whole disk at `/data`. Torrents added before the change still point at `/downloads/...` and show as *Missing files*. Fix them once in the qBittorrent UI: select all → right-click → **Set location…** → `/data/downloads` (or the matching category subfolder), then **Force recheck** any that stay in error. Files don't move — they're already on the same disk.

## First run

Each step needs the previous ones. Turn on authentication in every app the first time you open it.

### 1. VPN and qBittorrent

1. Fill in all of `.env` ([Environment variables](#environment-variables)), create the folders ([Bind mounts](#bind-mounts)), `docker compose up -d`, and [check the tunnel](#vpn-surfshark-through-gluetun).
2. Run the first Recyclarr sync now, so the quality profiles exist before you add or import anything (see [step 6](#6-quality-profiles-recyclarr)):
   ```bash
   docker compose exec recyclarr recyclarr sync
   ```
3. Get qBittorrent's temporary password with `docker logs qbittorrent`, log in at http://localhost:8881 as `admin` and change it (*Tools → Options → Web UI*).
4. *Tools → Options*:
   - *Downloads → Default Torrent Management Mode:* **Automatic**. *Default Save Path:* `/data/downloads`.
   - *Connection:* turn **off** "Use UPnP / NAT-PMP" (there's no router to talk to inside the tunnel).
   - *Advanced → Network interface:* `tun0` — a second kill switch at the client level.
5. Categories (left panel → right-click *Categories*): `radarr` → `/data/downloads/movies`, `sonarr` → `/data/downloads/series`.

### 2. Jellyfin

At http://localhost:8096: libraries on `/media/movies`, `/media/series`, … (**not** `/media/downloads`). Then *Dashboard → API Keys* → create one key each for Sonarr/Radarr and Maintainerr (Seerr logs in with your Jellyfin account instead).

### 3. Prowlarr

At http://localhost:9696:

1. *Settings → Indexers → Indexer Proxies → + → FlareSolverr*: name `FlareSolverr`, tag **`flaresolverr`**, host `http://flaresolverr:8191/`. Prowlarr only routes indexers carrying that tag through it.
2. *Indexers → Add Indexer* for each tracker. For Cloudflare-protected ones (1337x, …), add the **`flaresolverr`** tag in the indexer's form before *Test* — without it the test fails with *blocked by CloudFlare Protection*.

If FlareSolverr can't get through a site (`docker logs flaresolverr` shows the challenge timing out), [Byparr](https://github.com/ThePhaseless/Byparr) is a drop-in replacement: swap the image for `ghcr.io/thephaseless/byparr`, same port and API, no Prowlarr changes.

### 4. Sonarr and Radarr

At http://localhost:8989 and http://localhost:7878, for each:

1. *Settings → Media Management:* root folder `/data/series` (Sonarr) / `/data/movies` (Radarr); **Use Hardlinks instead of Copy** on; enable renaming.
2. *Settings → Download Clients → qBittorrent:* host **`gluetun`**, port `8881`, your qBittorrent credentials, category `sonarr` / `radarr`.
3. *Settings → Connect → Emby / Jellyfin:* host `jellyfin`, port `8096`, the Jellyfin API key, **Update Library** on, **Map Paths From** `/data/` **To** `/media/`.
4. Import what's already on disk: *Series → Library Import* `/data/series` / *Movies → Library Import* `/data/movies`, with quality profile **WEB-1080p** (Sonarr) / **HD Bluray + WEB** (Radarr).

### 5. Connect Prowlarr

In Prowlarr, *Settings → Apps* → add Sonarr (`http://sonarr:8989`) and Radarr (`http://radarr:7878`) with their API keys from `.env`, Prowlarr server `http://prowlarr:9696`. Indexers then sync on their own — don't add them in Sonarr/Radarr by hand.

### 6. Quality profiles (Recyclarr)

Nothing to click — the first sync already ran in step 1. Recyclarr creates the **WEB-1080p** profile in Sonarr and **HD Bluray + WEB** in Radarr, with TRaSH's custom formats and scores, and re-syncs them daily ([`recyclarr/recyclarr.yml`](./recyclarr/recyclarr.yml)). Then:

- Pick these profiles when adding; anything added before the first sync can be switched in bulk with *Mass Editor*.
- Don't hand-edit those profiles or their custom-format scores — the next sync overwrites them. Change `recyclarr.yml` instead.
- These profiles prefer the **original** language. If you want Spanish audio, TRaSH-Guides has language custom formats to add to `recyclarr.yml`.

Exportarr serves metrics from the start; restart Prometheus once to load the new alert rules: `docker compose -f ../monitoring/docker-compose.yml restart prometheus`.

### 7. Bazarr (subtitles)

At http://localhost:6767:

1. *Settings → Languages:* add the languages you want (e.g. Spanish, English), create a **Languages Profile** with them, and set it as the default for series and movies.
2. *Settings → Providers:* e.g. OpenSubtitles.com (free account), Subdivx (Spanish), Addic7ed (series), Podnapisi.
3. *Settings → Sonarr / Radarr:* host `sonarr` / `radarr`, ports `8989` / `7878`, API keys. No path mapping needed — Bazarr sees the same `/data`.

### 8. Seerr (requests)

At http://localhost:5055:

1. Sign in with **Jellyfin**: URL `http://jellyfin:8096`, your Jellyfin admin account.
2. Sync libraries, then *Settings → Services*: add Radarr (`radarr`, `7878`, profile *HD Bluray + WEB*, root `/data/movies`) and Sonarr (`sonarr`, `8989`, profile *WEB-1080p*, root `/data/series`), both marked **Default**.
3. *Users → Import Jellyfin users*; set auto-approve for yourself.
4. Optional: *Settings → Notifications → Telegram* to hear when requests are available.
5. To use it from outside the LAN, add a Proxy Host in [Nginx Proxy Manager](../nginx/README.md) to `seerr:5055` — the only media app worth exposing.

### 9. Searcharr (Telegram bot)

1. Until `settings.py` exists the `searcharr` container restarts in a loop — expected, ignore it.
2. In Telegram, ask @BotFather for a **new** bot token. Don't reuse the Alertmanager or Hermes bot: two programs polling one token kick each other off.
3. Create the config from the sample and fill it in:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/toddrob99/searcharr/main/settings-sample.py -o ~/searcharr/data/settings.py
   ```
   Set `tgram_token`, `searcharr_password`, `searcharr_admin_password`, `sonarr_url = "http://sonarr:8989"`, `radarr_url = "http://radarr:7878"`, the API keys, and the quality profile names (`WEB-1080p`, `HD Bluray + WEB`).
4. `docker compose restart searcharr`, then send `/start <admin password>` to the bot. Search with `/series <name>` or `/movie <name>`.

### 10. Cleanuparr (queue hygiene)

At http://localhost:11011, add qBittorrent (`http://gluetun:8881`), Sonarr and Radarr, then enable:

- **Queue Cleaner** — strikes for stalled / stuck-on-metadata / too-slow downloads (3 strikes is a good start); removed items are blocklisted so Sonarr/Radarr search again.
- **Malware Blocker** — drops known-malicious releases (`.exe`, `.lnk` and similar disguised as video).
- **Download Cleaner** — optional: remove finished torrents in the `sonarr`/`radarr` categories after a seeding time/ratio, and ones that are no longer hardlinked into the library.

### 11. Maintainerr (library cleanup)

At http://localhost:6246: connect Jellyfin (`http://jellyfin:8096` + API key), Sonarr, Radarr and Seerr. Rules put matching media into a collection and delete it after a grace period, e.g. *movies watched by all users and added more than 90 days ago → delete after 30 days*. Start with a long grace period and check what lands in the collections before trusting a rule.

## Hardware acceleration (optional, Jellyfin)

Jellyfin can transcode using the GPU by adding devices/drivers to the compose. For now it runs on CPU; if load demands it, extend later.
