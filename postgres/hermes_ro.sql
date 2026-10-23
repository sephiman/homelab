-- Read-only role for the Hermes agent (hermes/).
--
-- `hermes_ro` sees connections, locks and statistics (pg_monitor) and reads
-- every table in every database it can connect to (pg_read_all_data, which
-- also covers tables and databases created later). It holds no write privilege
-- anywhere. The vaultwarden database revokes CONNECT from PUBLIC, so it stays
-- out of reach — do the same for any future database Hermes must not read:
--   REVOKE ALL ON DATABASE <db> FROM PUBLIC;
--
-- Run once on the homelab host (pick your own password):
--   docker cp postgres/hermes_ro.sql postgresdb:/tmp/hermes_ro.sql
--   docker exec -it postgresdb psql -U root -d postgres \
--     -v ro_password='<password>' -f /tmp/hermes_ro.sql
--
-- Then put the same password in hermes/.env as HERMES_DB_RO_PASSWORD.
--
-- Upgrading a role created before data access was added: run only the
-- `ALTER ROLE … statement_timeout` and `GRANT pg_read_all_data` lines.

\set ON_ERROR_STOP on

CREATE ROLE hermes_ro LOGIN PASSWORD :'ro_password' CONNECTION LIMIT 3;
ALTER ROLE hermes_ro SET default_transaction_read_only = on;
ALTER ROLE hermes_ro SET statement_timeout = '30s';
ALTER ROLE hermes_ro SET idle_in_transaction_session_timeout = '60s';

GRANT pg_monitor TO hermes_ro;
GRANT pg_read_all_data TO hermes_ro;
