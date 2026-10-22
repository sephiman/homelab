# Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) as a Telegram ops assistant for this
homelab. The model is your choice — any provider Hermes supports; see
[Choosing a model](#choosing-a-model) for free options. It can look at containers, logs, host health and Postgres
statistics, and restart or update the Docker Compose projects on the host — the stacks in this
repo and any other app deployed there. Nothing more.

Unlike the other folders this is **not a compose stack**: Hermes runs on the host as an
unprivileged `hermes` user, so that Linux (not the agent's own judgement) enforces what it can do.
Running it in Docker would mean mounting the Docker socket (root on the host), and Hermes skips its
dangerous-command checks on container backends.

## Security model

| Allowed | How |
|---------|-----|
| Project list, container list, per-project status and logs, `git-status` (pending commits), filtered `inspect`, `docker.service` journal | `sudo -n homelab-manage.sh <action>` — the only sudo rule `hermes` has |
| `restart` / `update` (git pull + pull + up --build) of any Compose project, `restart-docker` | Same wrapper; it finds the project's folder and compose files from Docker's own labels |
| `uptime`, `free`, `df`, `top`, `systemctl status docker` | Plain commands, no privilege needed |
| Home Assistant: entity states (`ha-states`, `ha-state`), logbook (`ha-logbook`), automations (`ha-automations`), log (`ha-log`), switching lights (`ha-light`) | The wrapper holds a token Hermes never sees; its only write is `light.turn_on/off/toggle` on `light.*`; tokens, image URLs and GPS coordinates are filtered out |
| Postgres connections, locks and stats; listing tables | `hermes_ro` role: `pg_monitor`, read-only transactions, 15 s timeout, 3 connections |
| Messages from your Telegram account only | `TELEGRAM_ALLOWED_USERS`; everyone else is ignored silently |

| Denied | How |
|--------|-----|
| Any direct Docker command (`prune`, `down -v`, `rm -f`, `run -v /:/host`, …) | `hermes` is not in the `docker` group and has no socket access |
| Root shell, `/etc`, firewall, formatting disks | sudoers allows only the wrapper; the wrapper rejects any other argument |
| `.env` files (of every project), `ha_token`, Vaultwarden data, rclone config | The installer sets `chmod 600` on `.env` files in each project folder and checks that the project owners' homes are closed to other users |
| Container env vars via `docker inspect` (`POSTGRES_PASSWORD`, `ADMIN_TOKEN`, …) | The wrapper's `inspect` prints a fixed template that never includes env |
| `INSERT` / `UPDATE` / `DELETE` / `DROP` / `ALTER` / `TRUNCATE`, reading table data | `hermes_ro` has no table grants; `vaultwarden` DB revokes `CONNECT` from `PUBLIC` |
| LAN, other host services, Docker networks | nftables egress rule for the `hermes` uid: DNS, `127.0.0.1:5432` and outbound 443 only |

Hermes' own settings in [`config.yaml`](./config.yaml), installed root-owned as its managed config
so the agent can't change them, add a second layer: `approvals.mode: manual`
(you approve dangerous commands from Telegram), `deny` globs for Docker/firewall/secrets, no
auto-approval in cron or one-shot sessions, and `HERMES_WRITE_SAFE_ROOT` limiting file writes to
its own directories. Treat these as a seatbelt, not the wall.

### What to know before relying on it

- **Wrapper actions are not prompted.** Hermes' approval prompt only fires on its built-in
  dangerous patterns; `sudo -n homelab-manage.sh restart media` doesn't match any. The agent's
  instructions ([`AGENTS.md`](./AGENTS.md)) tell it to ask first, but the hard guarantee is only
  that the worst it can do is restart or update an existing Compose project (or the Docker
  daemon). That includes any app you deploy on the host, so live bots or anything with state
  you care about are in reach of a restart.
- **Logs are attacker-controlled text.** Nginx access logs carry strings from the internet; a
  crafted line could try to steer the agent into a restart. That is why the wrapper is so narrow.
- **`update` pulls the whole git checkout** the project lives in (`git pull --ff-only` as the
  folder's owner; skipped if it isn't a checkout), then rebuilds that one project. Hermes cannot
  push or edit any checkout — it cannot even read them.
- **Only projects that have containers are reachable.** The folder comes from the labels Compose
  puts on its containers, so a project that was taken `down` completely can't be found until you
  bring it up once by hand. Hermes can never start a project that was never deployed.
- **`restart-docker` restarts every container**, Postgres included.
- Every wrapper call is logged: `journalctl -t homelab-manage`.

## Files

| File | Installed as |
|------|--------------|
| `install-host.sh` | Run once as root; sets up everything below |
| `homelab-manage.sh` | `/usr/local/bin/homelab-manage.sh` (root, 0755) |
| `hermes.sudoers` | `/etc/sudoers.d/hermes` |
| `hermes-egress.nft`, `hermes-egress.service` | `/etc/hermes/hermes-egress.nft`, a oneshot unit that loads it at boot |
| `hermes-gateway.service` | `/etc/systemd/system/hermes-gateway.service` |
| `config.yaml` | `/etc/hermes/config.yaml` — Hermes' managed config (root-owned; wins over its own config) |
| `.env.example` → `.env` | `/home/hermes/.hermes/.env` and `/home/hermes/.pgpass` |
| `AGENTS.md` | `/home/hermes/workspace/AGENTS.md` — the agent's operating instructions (loaded at session start; `/new` after changing it) |
| [`../postgres/hermes_ro.sql`](../postgres/hermes_ro.sql) | Run once inside `postgresdb` |

## Install

Prerequisites: the `postgres` stack is up, and you have a Telegram bot token
([@BotFather](https://t.me/BotFather)), your numeric Telegram user id
([@userinfobot](https://t.me/userinfobot)) and an API key for the model provider you pick ([Choosing a model](#choosing-a-model)).

1. **Read-only database role** (pick a password):

   ```bash
   docker cp postgres/hermes_ro.sql postgresdb:/tmp/hermes_ro.sql
   docker exec -it postgresdb psql -U root -d postgres \
     -v ro_password='<password>' -f /tmp/hermes_ro.sql
   ```

2. **Secrets:**

   ```bash
   cd hermes
   cp .env.example .env    # fill in all four values; same DB password as step 1
   ```

3. **Host setup** (user, wrapper, sudoers, firewall, units, secrets):

   ```bash
   sudo ./install-host.sh
   ```

   It stops if your home directory is readable by other users or if `hermes` is in a privileged
   group, and tells you the fix.

4. **Install Hermes as the `hermes` user.** Read the installer first; it needs no root:

   ```bash
   curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh
   less /tmp/hermes-install.sh
   sudo -iu hermes bash /tmp/hermes-install.sh --non-interactive --skip-browser --skip-computer-use
   ```

5. **Check the model and start the gateway:**

   ```bash
   sudo -iu hermes /home/hermes/.local/bin/hermes config    # managed keys from /etc/hermes/config.yaml
   sudo systemctl enable --now hermes-gateway
   journalctl -u hermes-gateway -f
   ```

   Don't use `hermes gateway install` — the repo's unit already runs it as `hermes`, behind the
   egress firewall, with the write root set.

## Verify

Run these on the host after installing; each line says what you should see.

```bash
sudo -iu hermes docker ps                              # permission denied on the Docker socket
sudo -iu hermes sudo -n /usr/bin/id                    # "a password is required" — no other sudo
sudo -iu hermes sudo -n /usr/local/bin/homelab-manage.sh ps              # container table
sudo -iu hermes sudo -n /usr/local/bin/homelab-manage.sh restart foo     # no compose project 'foo'
sudo -iu hermes sudo -n /usr/local/bin/homelab-manage.sh inspect postgresdb | grep -i password  # nothing
sudo -iu hermes cat /home/juanjo/docker/homelab/postgres/.env  # permission denied (adjust to your repo path)
sudo -iu hermes psql service=homelab -c 'select count(*) from pg_stat_activity'  # a number
sudo -iu hermes psql service=homelab -c 'create table t(x int)'      # read-only transaction error
sudo -iu hermes psql "service=homelab dbname=vaultwarden" -c 'select 1'  # permission denied for database
sudo -iu hermes curl -sS -m 5 http://192.168.1.1        # times out (LAN blocked)
sudo -iu hermes curl -sS -m 5 https://api.telegram.org  # responds
```

Then from Telegram: message the bot from your account (it answers) and from another account
(silence).

## Home Assistant

Hermes can read entity states ("¿está encendida la luz del salón?", "temperature in the
bedroom?"), read the logbook (what changed when, and what caused it), check automations and the
Home Assistant log for problems, and switch **lights** on or
off. It can't touch other devices, automations or configuration. A Home Assistant token can do far
more than that, so it never reaches Hermes: `install-host.sh` stores it root-only in
`/etc/homelab-manage/ha_auth_header`, and only the wrapper uses it — reads, plus
`light.turn_on` / `turn_off` / `toggle` on `light.*` entities. The log comes from the
`homeassistant` container's output (`docker logs`), since the compose file discards the log file.

1. In Home Assistant, create a user `hermes` (Settings → People → Users; **not** an administrator).
   Log in as it, open its profile → Security → **Long-lived access tokens** → Create token.
2. Put it in `hermes/.env` as `HA_TOKEN=…`, then:
   ```bash
   sudo ./install-host.sh && sudo systemctl restart hermes-gateway
   sudo -iu hermes sudo -n /usr/local/bin/homelab-manage.sh ha-states light
   ```
3. `/new` in each chat so the agent sees the new actions.

The non-admin user limits what the token could do if it ever leaked (no configuration changes),
but it could still switch any device — which is why it stays root-only. Lights that Home Assistant
exposes as `switch.*` (smart plugs) aren't covered; add their domain to `ha-light` only if you
want them switchable.

## Choosing a model

Whatever model you use receives your logs, container names and DB stats, so its data terms matter
as much as its price.

| Provider (`model.provider`) | Model id | Cost | Catch |
|---|---|---|---|
| OpenRouter (`openrouter`) | `nvidia/nemotron-3-ultra-550b-a55b:free` *(default)*; other `:free` models with tool support, e.g. `minimax/minimax-m3.1-flash:free` | Free | 50 requests/day (20/min) until you buy 10 credits once, then 1,000/day. Free providers may log or train on prompts — check the model page and OpenRouter's privacy settings |
| Google Gemini (`gemini`) | `gemini-3.8-flash`, `gemini-2.5-flash` | Free tier | Free-tier content **is used to improve Google's products**; low quotas, and an agent turn makes several calls |
| OpenCode Zen (`opencode-zen`) | `glm-5.3-flash` | $0.15 / $0.50 per 1M tokens in/out, prepaid credit | Paid only from Hermes: Zen's `*-free` models answer `403 … can only be used from within OpenCode` |

Prices and free tiers change; check [OpenRouter models](https://openrouter.ai/models?max_price=0),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[OpenCode Zen](https://opencode.ai/docs/zen/). One OpenRouter key also covers paid models, so
moving off a free model is just a different model id. Small models are weaker at multi-step tool use:
if the bot calls the wrapper wrongly or stops asking before restarts, try a stronger one.

To switch, edit `model:` in this folder's `config.yaml` (the key always goes in `LLM_API_KEY`):

```bash
nano hermes/config.yaml                               # model.provider / model.default
nano hermes/.env                                      # LLM_API_KEY=<that provider's key>
sudo ./install-host.sh                                # from hermes/; prints which variable it used
sudo systemctl restart hermes-gateway
```

`hermes model` / `hermes config set` refuse to change these keys: `config.yaml` is installed as
Hermes' managed config (`/etc/hermes/config.yaml`). `install-host.sh` reads the provider from it
and saves `LLM_API_KEY` as that provider's variable. It knows `opencode-zen`, `opencode-go`, `gemini`, `openrouter`, `anthropic`,
`openai-api` and `deepseek`; for any other provider it stops and asks you to put the provider's own
key variable in `hermes/.env` instead (any variable there is passed through as-is).

`/model <id>` in Telegram switches only the current chat. Cloud APIs need nothing else; a local model (Ollama on the LAN or host) is blocked by the
egress firewall until you add a rule for it in `hermes-egress.nft`.

### Fallback model

When the primary fails (rate limit, outage), Hermes switches to the first entry of
`fallback_providers` in `config.yaml` and goes back to the primary once its limit resets. The
default is Gemini `gemini-3.5-flash-lite` on Google's free tier: fast and cheap on quota, with a
context large enough for Hermes' requests — but free-tier content **is used to improve Google's
products**, so your logs reach Google while the fallback is active.

The fallback must be a **different provider**: OpenRouter's free limit (`free-models-per-day`: 50
requests, or 1,000 after buying 10 credits once) is per account, so no other `:free` model helps.
Groq's free plan doesn't work either: its tokens-per-minute cap is smaller than a single Hermes
request, which fails with `413 Request payload too large`.

Change the fallback in this folder's `config.yaml` (`fallback_providers:`) and put its key in
`FALLBACK_API_KEY`; `install-host.sh` maps it like `LLM_API_KEY`:

```bash
nano hermes/.env                                              # FALLBACK_API_KEY=<Gemini key>
sudo ./install-host.sh           # prints: FALLBACK_API_KEY -> GEMINI_API_KEY (provider gemini)
sudo systemctl restart hermes-gateway
sudo -iu hermes /home/hermes/.local/bin/hermes fallback list
```

Don't use `hermes fallback add` / `hermes model` for this: the managed config wins anyway, and their
custom-endpoint picker stores keys in generated variables in Hermes' own `.env`, which the next
`install-host.sh` run wipes.

## Using it

Talk to the bot in plain language: "¿qué contenedores están caídos?", "últimos 200 logs de
monitoring", "¿hay bloqueos en Postgres?", "actualiza media". It proposes a restart or update and
waits for your yes.

### In a Telegram group

- Keep **your user id** in `TELEGRAM_ALLOWED_USERS`: it matches the sender, so it is what lets you
  command the bot in a group too. A group id there does nothing.
- Optionally pin the bot to that group with `TELEGRAM_ALLOWED_CHATS=-100…` (see `.env.example`).
  Don't use `TELEGRAM_GROUP_ALLOWED_CHATS`: it authorizes every member of the group.
- With BotFather's privacy mode on (the default) Telegram delivers only `/commands@yourbot`,
  replies to the bot and service messages — **not** `@mentions` or plain text, and the member list
  says the bot "has no access to messages". Turn it off: @BotFather → `/setprivacy` → your bot →
  Disable, then **remove the bot from the group and add it back** (the change only applies to
  groups joined afterwards).
- `TELEGRAM_REQUIRE_MENTION=false` (the default here) answers every message you send in the group;
  `true` answers only `@yourbot …` and replies to it — useful if you also chat with people there,
  since every message you send otherwise spends model requests.
- Other members' messages then reach the gateway but are dropped (`Blocked unauthorized user` in
  the journal) and don't enter the model's context.
- Quick delivery test: `/help@yourbot` in the group answers even with privacy mode on.
- Everyone in the group can read its replies (log excerpts, container names, DB stats).
- After editing `hermes/.env`: `sudo ./install-host.sh && sudo systemctl restart hermes-gateway`.

To let it read the data of one specific database (beyond listing tables), grant it explicitly,
the same way `grafana_ro.sql` does:

```sql
\connect <database>
GRANT USAGE ON SCHEMA public TO hermes_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_ro;
```

## Maintenance

- **Update Hermes:** `sudo -iu hermes /home/hermes/.local/bin/hermes update`, then
  `sudo systemctl restart hermes-gateway`.
- **Changed a file in this folder:** run `sudo ./install-host.sh` again, then
  `sudo systemctl restart hermes-gateway` (Hermes reads config and `.env` at startup). For
  `AGENTS.md`, also `/new` in each chat.
- **Settings:** `hermes config` shows which keys are managed. Keys not in `config.yaml` stay
  editable with `hermes config set`; to pin one, add it to `config.yaml`.
- **Rotate a secret:** edit `hermes/.env`, re-run `install-host.sh`, restart the gateway.
- **Stop it now:** `sudo systemctl stop hermes-gateway` (or revoke the token in @BotFather).

## Uninstall

```bash
sudo systemctl disable --now hermes-gateway hermes-egress
sudo rm /etc/systemd/system/hermes-{gateway,egress}.service /etc/sudoers.d/hermes \
  /usr/local/bin/homelab-manage.sh
sudo rm -r /etc/hermes /etc/homelab-manage
sudo systemctl daemon-reload
sudo userdel -r hermes
docker exec -it postgresdb psql -U root -d postgres -c 'DROP ROLE hermes_ro'
```
