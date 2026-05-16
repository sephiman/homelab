# Media

Homelab media stack:

- [Jellyfin](https://jellyfin.org/) — media server (free alternative to Plex/Emby).
- [qBittorrent](https://www.qbittorrent.org/) ([linuxserver.io build](https://docs.linuxserver.io/images/docker-qbittorrent/)) — BitTorrent client with a web UI that downloads straight into the library.

## Services

| Service     | Container     | Host port                       | Access                |
|-------------|---------------|---------------------------------|-----------------------|
| jellyfin    | `jellyfin`    | `8096`                          | http://localhost:8096 |
| qbittorrent | `qbittorrent` | `8881` (UI), `6881` TCP+UDP     | http://localhost:8881 |

- `8881` — qBittorrent web UI.
- `6881` (TCP+UDP) — client's listen port for peers. For better connectivity, forward this port on your router to the host.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## Bind mounts

Service config lives under the home directory of whoever runs Docker (`${HOME}` is interpolated by Compose at parse time). The library itself lives on an external mount point (`/mnt/media`), independent of any user.

### Jellyfin

- `${HOME}/jellyfin/config` → `/config` — config, users, metadata, DB.
- `${HOME}/jellyfin/cache`  → `/cache`  — transcodes and thumbnails. Safe to delete.
- `/mnt/media`              → `/media`  — media library (read).

### qBittorrent

- `${HOME}/qbittorrent/config` → `/config`    — settings, torrent DB, categories.
- `/mnt/media/downloads`       → `/downloads` — download destination (subfolder of the library).

> Both share `/mnt/media`: qBittorrent writes into `/mnt/media/downloads`, Jellyfin reads all of `/mnt/media`. New downloads appear in Jellyfin after a *Scan Library* (manual or scheduled). Make sure the paths exist and that `PUID=1000`/`PGID=1000` has permissions on them.
>
> Create the host folders before the first `up` so Docker doesn't create them owned by root:
>
> ```bash
> mkdir -p "$HOME/jellyfin/config" "$HOME/jellyfin/cache" "$HOME/qbittorrent/config"
> sudo mkdir -p /mnt/media/downloads
> sudo chown -R 1000:1000 /mnt/media
> ```

## Environment variables (qBittorrent)

- `PUID` / `PGID` — uid/gid of the host user that owns the volumes (1000 by default; adjust if yours differs).
- `TZ` — container timezone (`Europe/Amsterdam`).
- `WEBUI_PORT` — internal UI port; must match the published one (`8881`).

## Network

Both services join `all_dockers` so they can be reverse-proxied by Nginx Proxy Manager without exposing their ports externally.

## Bring it up

```bash
docker compose up -d
```

## First run

### Jellyfin

At http://localhost:8096:

1. Create the admin user.
2. Add libraries pointing at subfolders of `/media` (`/media/movies`, `/media/series`, …). It's a good idea to **exclude** `/media/downloads` so in-progress files don't pollute the library.
3. Configure metadata providers (TMDb, TheTVDB, etc.).

### qBittorrent

The linuxserver build generates a temporary password on first boot. Find it with:

```bash
docker logs qbittorrent
```

Look for the temporary password line for user `admin`, log in at http://localhost:8881 and change it under *Tools → Options → Web UI*.

After that, it's worth setting:

- *Downloads → Default Save Path:* `/downloads`
- Separate categories (e.g. `movies` → `/downloads/movies`, `series` → `/downloads/series`) to keep things tidy.

## Suggested `/mnt/media` layout

```
/mnt/media/
├── movies/
├── series/
├── music/
└── downloads/          # qBittorrent destination
    ├── movies/
    └── series/
```

## Hardware acceleration (optional, Jellyfin)

Jellyfin can transcode using the GPU by adding devices/drivers to the compose. For now it runs on CPU; if load demands it, extend later.
