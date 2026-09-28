-- Role and database for the vaultwarden stack.
--
-- Run once on the homelab host (use the same password as VAULTWARDEN_DB_PASSWORD
-- in vaultwarden/.env):
--   docker cp postgres/vaultwarden.sql postgresdb:/tmp/vaultwarden.sql
--   docker exec -it postgresdb psql -U root -d postgres \
--     -v vw_password='<password>' -f /tmp/vaultwarden.sql
--
-- Vaultwarden creates and migrates its own tables on startup.

\set ON_ERROR_STOP on

CREATE ROLE vaultwarden LOGIN PASSWORD :'vw_password';
CREATE DATABASE vaultwarden OWNER vaultwarden;
REVOKE ALL ON DATABASE vaultwarden FROM PUBLIC;
