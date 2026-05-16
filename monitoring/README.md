# Monitoring

Homelab management and (eventually) observability stack.

## Current state

| Service   | Container   | Host port      | Access                                         |
|-----------|-------------|----------------|------------------------------------------------|
| portainer | `portainer` | `9000`, `9443` | http://localhost:9000 · https://localhost:9443 |

[Portainer CE](https://www.portainer.io/) provides a UI to manage Docker containers, images, volumes and networks.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## Persistence

- `portainer_data` — Docker-managed named volume with Portainer's configuration, users and registered endpoints.

## Docker socket

`/var/run/docker.sock` is bind-mounted into the container so Portainer can talk to the daemon. This grants Portainer full control over Docker on the host: treat UI access as root access.

> On Windows with Docker Desktop the socket bind works the same way (Docker Desktop exposes the Linux socket through WSL2).

## Bring it up

```bash
docker compose up -d
```

On the first run Portainer asks you to create the admin user through the UI (do it within the first few minutes or the initial setup window expires).

## Pending

Add to this stack later, no rush:

- **Loki** — log aggregation.
- **Prometheus** — metrics.
- **Grafana** — dashboards on top of Loki + Prometheus.
