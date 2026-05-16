# Nginx Proxy Manager

Reverse proxy with a web UI for managing hosts, redirects and Let's Encrypt certificates without editing config files by hand.

## Service

| Service | Container             | Host port           | Access                    |
|---------|-----------------------|---------------------|---------------------------|
| app     | `nginx-proxy-manager` | `80`, `443`, `81`   | UI at http://localhost:81 |

- `80` / `443` — HTTP/HTTPS traffic for the proxied sites.
- `81` — admin UI.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## Persistence (bind mounts)

- `./data` — NPM configuration, SQLite database, defined hosts.
- `./letsencrypt` — ACME account and issued certificates.

Both folders are listed in `.gitignore`. Back them up regularly.

## Networks

- `my-network` (alias of the external `all_dockers`) — used to reverse-proxy any other homelab stack by container name.
- `custom_bridge` (own bridge) — extra local bridge network, useful to isolate specific traffic if needed.

## Bring it up

```bash
docker compose up -d
```

## First run

Default UI credentials (change them on first login):

- Email: `admin@example.com`
- Password: `changeme`

## Proxy a homelab service

Because NPM is on `all_dockers`, you point a *Proxy Host* at the destination container's hostname. Example for pgAdmin:

- **Forward Hostname / IP:** `pgadmin`
- **Forward Port:** `80`

No need to publish the destination service's port to the host.
