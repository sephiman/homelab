# Home Automation

Smart-home stack built around [Home Assistant](https://www.home-assistant.io/) with a Zigbee bridge:

- [Home Assistant](https://www.home-assistant.io/) — open-source home automation hub.
- [Zigbee2MQTT](https://www.zigbee2mqtt.io/) — bridges a Zigbee USB coordinator to MQTT so Home Assistant can talk to Zigbee devices without a vendor cloud.
- [Eclipse Mosquitto](https://mosquitto.org/) — the MQTT broker that sits between Zigbee2MQTT and Home Assistant.

```
Zigbee devices  ──(radio)──>  CC2531/Sonoff dongle  ──(USB)──>  zigbee2mqtt  ──(MQTT)──>  mosquitto  ──(MQTT)──>  homeassistant
```

## Services

| Service       | Container       | Host ports                       | Access                |
|---------------|-----------------|----------------------------------|-----------------------|
| homeassistant | `homeassistant` | `8123`                           | http://localhost:8123 |
| zigbee2mqtt   | `zigbee2mqtt`   | `8124`                           | http://localhost:8124 |
| mosquitto     | `mqtt`          | `1883` (MQTT), `9001` (WS)       | `mqtt://localhost:1883` |

- `1883` — plain MQTT (used by Zigbee2MQTT and Home Assistant).
- `9001` — MQTT over WebSockets (handy for browser-based clients).
- `8124` — Zigbee2MQTT web frontend (pairing, device list, OTA updates).
- `8123` — Home Assistant web UI.

## Prerequisite

The shared network must exist before bringing the stack up:

```bash
docker network create all_dockers
```

See the [root README](../README.md#shared-network-all_dockers) for details. Run this once per host.

## Zigbee coordinator (USB dongle)

The `zigbee2mqtt` container needs a Zigbee USB coordinator (e.g. Sonoff Zigbee 3.0 Dongle Plus, CC2652, ConBee II) plugged into the host. The compose maps it as:

```yaml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

If your dongle shows up under a different path, edit `docker-compose.yml`. To find it:

```bash
ls -l /dev/serial/by-id/
```

For a stable path that survives reboots, prefer the `/dev/serial/by-id/...` symlink over `/dev/ttyUSB0`.

`/run/udev` is mounted read-only so Zigbee2MQTT can resolve device names through udev.

`privileged: true` is set on `zigbee2mqtt` (USB access) and on `homeassistant` (needed by some integrations such as Bluetooth and USB-attached hardware).

## Bind mounts

Config and state live under the home directory of whoever runs Docker (`${HOME}` is interpolated by Compose at parse time), matching the convention used by the other stacks in this repo.

### Home Assistant

- `${HOME}/homeassistant/config` → `/config` — full HA configuration directory (`configuration.yaml`, integrations, secrets, DB).
- `/etc/localtime` → `/etc/localtime` (ro) — keeps the container clock aligned with the host timezone.

### Zigbee2MQTT

- `${HOME}/homeassistant/zigbee/zigbee2mqtt/zigbee2mqtt-data` → `/app/data` — coordinator state, paired devices, `configuration.yaml`.

### Mosquitto

- `${HOME}/homeassistant/zigbee/mosquitto/config` → `/mosquitto/config` — `mosquitto.conf`, ACLs, password file.
- `${HOME}/homeassistant/zigbee/mosquitto/data` → `/mosquitto/data`   — retained messages, persistence DB.
- `${HOME}/homeassistant/zigbee/mosquitto/log`  → `/mosquitto/log`    — broker logs.

Create the host folders before the first `up` so Docker doesn't create them owned by root:

```bash
mkdir -p \
  "$HOME/homeassistant/config" \
  "$HOME/homeassistant/zigbee/mosquitto/config" \
  "$HOME/homeassistant/zigbee/mosquitto/data" \
  "$HOME/homeassistant/zigbee/mosquitto/log" \
  "$HOME/homeassistant/zigbee/zigbee2mqtt/zigbee2mqtt-data"
```

## Mosquitto minimal config

The broker needs at least a `mosquitto.conf` to listen on `1883` and (optionally) on `9001` for WebSockets. Drop the file at `$HOME/homeassistant/zigbee/mosquitto/config/mosquitto.conf`:

```conf
listener 1883
listener 9001
protocol websockets

persistence true
persistence_location /mosquitto/data/
log_dest file /mosquitto/log/mosquitto.log

# For a quick start (LAN-only). Replace with password_file + ACLs for anything real.
allow_anonymous true
```

For production, create credentials with `mosquitto_passwd` and set `allow_anonymous false` + `password_file /mosquitto/config/passwd`.

## Network

All three services join `all_dockers`, so:

- `zigbee2mqtt` reaches the broker at `mqtt://mqtt:1883` (container name).
- Home Assistant's MQTT integration also points to `mqtt://mqtt:1883`.
- Nginx Proxy Manager can expose `homeassistant:8123` and `zigbee2mqtt:8124` externally without publishing extra host ports.

## Bring it up

```bash
docker compose up -d
docker compose logs -f
```

`zigbee2mqtt` `depends_on: mosquitto`, so Compose starts the broker first. Home Assistant doesn't depend on either — it'll start and you wire MQTT up in the UI afterwards.

## First run

### Zigbee2MQTT

Open http://localhost:8124. The default `configuration.yaml` (generated on first boot inside `/app/data`) usually needs at least:

```yaml
mqtt:
  base_topic: zigbee2mqtt
  server: mqtt://mqtt:1883

serial:
  port: /dev/ttyUSB0   # or the /dev/serial/by-id/... path

frontend:
  port: 8124

permit_join: false     # flip to true only while pairing devices
```

Restart the container after editing. Toggle *Permit join* from the UI when adding new devices.

### Home Assistant

Open http://localhost:8123 and:

1. Complete the onboarding (admin user, location, units).
2. *Settings → Devices & services → Add integration → MQTT*. Broker: `mqtt`, port `1883` (anonymous, or with the user you created in Mosquitto).
3. With MQTT discovery enabled (Zigbee2MQTT publishes it by default), paired Zigbee devices show up automatically.

## Backups

The interesting state lives under:

- `${HOME}/homeassistant/config` — HA configuration, automations, DB.
- `${HOME}/homeassistant/zigbee/zigbee2mqtt/zigbee2mqtt-data` — coordinator network key + device list. **Losing this means re-pairing every Zigbee device.**
- `${HOME}/homeassistant/zigbee/mosquitto/data` — retained MQTT messages.

Back up at least the first two before any major change or host migration.
