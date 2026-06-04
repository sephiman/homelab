-- Read-only Grafana role for the crypto-ambush dashboards.
--
-- Creates `grafana_ro` with SELECT-only access to the three trading databases.
-- Defense in depth: read-only transactions by default, a statement timeout so
-- a pathological dashboard query can't pile up, and a connection cap.
--
-- Run once on the homelab host (pick your own password):
--   docker cp postgres/grafana_ro.sql postgresdb:/tmp/grafana_ro.sql
--   docker exec -it postgresdb psql -U root -d postgres \
--     -v ro_password='<password>' -f /tmp/grafana_ro.sql
--
-- Then put the same password in monitoring/.env as AMBUSH_DB_RO_PASSWORD and
-- `docker compose up -d grafana` in monitoring/.

\set ON_ERROR_STOP on

CREATE ROLE grafana_ro LOGIN PASSWORD :'ro_password' CONNECTION LIMIT 5;
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
ALTER ROLE grafana_ro SET statement_timeout = '10s';

GRANT CONNECT ON DATABASE crypto_ambush TO grafana_ro;
GRANT CONNECT ON DATABASE crypto_ambush_1h TO grafana_ro;
GRANT CONNECT ON DATABASE crypto_ambush_signals TO grafana_ro;

-- crypto_ambush: 15m live bot (amb_live_signals).
\connect crypto_ambush
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
-- Tables the bots create later (init_db micro-migrations) stay readable.
ALTER DEFAULT PRIVILEGES FOR ROLE root IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;

-- crypto_ambush_1h: 1h live bot (amb_live_signals).
\connect crypto_ambush_1h
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE root IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;

-- crypto_ambush_signals: Binance signal-only bot (amb_signal_alerts).
\connect crypto_ambush_signals
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE root IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
