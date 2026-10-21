-- Read-only monitoring role for the Hermes agent (hermes/).
--
-- `hermes_ro` can see connections, locks and statistics (pg_monitor) and list
-- tables in any database open to PUBLIC, but holds no SELECT on table data and
-- no write privilege anywhere. The vaultwarden database already revokes
-- CONNECT from PUBLIC, so it stays out of reach.
--
-- Run once on the homelab host (pick your own password):
--   docker cp postgres/hermes_ro.sql postgresdb:/tmp/hermes_ro.sql
--   docker exec -it postgresdb psql -U root -d postgres \
--     -v ro_password='<password>' -f /tmp/hermes_ro.sql
--
-- Then put the same password in hermes/.env as HERMES_DB_RO_PASSWORD.

\set ON_ERROR_STOP on

CREATE ROLE hermes_ro LOGIN PASSWORD :'ro_password' CONNECTION LIMIT 3;
ALTER ROLE hermes_ro SET default_transaction_read_only = on;
ALTER ROLE hermes_ro SET statement_timeout = '15s';
ALTER ROLE hermes_ro SET idle_in_transaction_session_timeout = '60s';

GRANT pg_monitor TO hermes_ro;
