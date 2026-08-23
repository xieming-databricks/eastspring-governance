-- Seed a DELIBERATELY PARTIAL live state for the config/uat e2e.
-- Run in the test workspace (SQL editor / notebook). Catalog `asset_prod` is a
-- throwaway; tear it down with dev-tools/teardown_uat.sql afterward.
--
-- Designed so `reconcile(config/uat)` produces every delta type + preflight warnings:
--   tags     : CHANGE (restricted_positions), REMOVE (acct identifier), ADD (region, legal_entity)
--   policies : CHANGE (mask fn differs), REMOVE (extra acm_policy_), ADD (rls policy absent)
--   grants   : all 12 target grants -> ADD (config groups hera-adbk-* don't exist here);
--              the current-user grant is UNMANAGED and must be ignored by the diff
--   warnings : volume asset_prod.gold.exports + function acm_filter_region_legal_entity absent

-- ---- catalog + schemas --------------------------------------------------
CREATE CATALOG IF NOT EXISTS asset_prod;
CREATE SCHEMA  IF NOT EXISTS asset_prod.gold;
CREATE SCHEMA  IF NOT EXISTS asset_prod.governance;

-- ---- objects (columns match what config/uat references) -----------------
CREATE TABLE IF NOT EXISTS asset_prod.gold.investment_accounts
  (acct STRING, client_name STRING, region STRING, legal_entity STRING);
CREATE TABLE IF NOT EXISTS asset_prod.gold.restricted_positions (position_id STRING);
CREATE VIEW  IF NOT EXISTS asset_prod.gold.shared_reference AS
  SELECT acct FROM asset_prod.gold.investment_accounts;

CREATE OR REPLACE FUNCTION asset_prod.gold.valuation_label(x STRING) RETURNS STRING RETURN x;
CREATE OR REPLACE FUNCTION asset_prod.governance.acm_mask_full_name_investments(v STRING)
  RETURNS STRING RETURN concat(substr(v, 1, 1), '***');
CREATE OR REPLACE FUNCTION asset_prod.governance.acm_mask_full_name_old(v STRING)
  RETURNS STRING RETURN 'X***';
-- INTENTIONALLY NOT CREATED (preflight warnings):
--   VOLUME   asset_prod.gold.exports
--   FUNCTION asset_prod.governance.acm_filter_region_legal_entity

-- ---- tags ---------------------------------------------------------------
-- correct matches (no delta):
ALTER TABLE asset_prod.gold.investment_accounts SET TAGS ('acm_business_function' = 'investments');
ALTER VIEW  asset_prod.gold.shared_reference     SET TAGS ('acm_business_function' = 'investments');
ALTER TABLE asset_prod.gold.investment_accounts ALTER COLUMN client_name SET TAGS ('acm_pii_type' = 'FULL_NAME');
-- CHANGE (target = investments):
ALTER TABLE asset_prod.gold.restricted_positions SET TAGS ('acm_business_function' = 'compliance');
-- REMOVE (extra acm_ tag not in config):
ALTER TABLE asset_prod.gold.investment_accounts ALTER COLUMN acct SET TAGS ('acm_pii_type' = 'identifier');
-- INTENTIONALLY OMITTED -> ADD: region (acm_rls=region), legal_entity (acm_rls=legal_entity)

-- ---- ABAC policies (requires ABAC-capable compute) ----------------------
-- CHANGE: target policy name, but points at the OLD mask function.
CREATE POLICY acm_policy_mask_full_name__h704960782f ON SCHEMA asset_prod.gold
  COLUMN MASK asset_prod.governance.acm_mask_full_name_old
  TO `account users`
  FOR TABLES
  WHEN has_tag_value('acm_business_function', 'investments')
  MATCH COLUMNS has_tag_value('acm_pii_type', 'FULL_NAME') AS c
  ON COLUMN c;
-- REMOVE: an acm_policy_* not present in config.
CREATE POLICY acm_policy_mask_extra__hdeadbeef01 ON SCHEMA asset_prod.gold
  COLUMN MASK asset_prod.governance.acm_mask_full_name_investments
  TO `account users`
  FOR TABLES
  WHEN has_tag_value('acm_business_function', 'investments')
  MATCH COLUMNS has_tag_value('acm_pii_type', 'FULL_NAME') AS c
  ON COLUMN c;
-- INTENTIONALLY OMITTED -> ADD: acm_policy_rls_region_legal_entity__he5f462fded

-- ---- grants -------------------------------------------------------------
-- Unmanaged principal (not a config group) -> the diff MUST ignore this.
GRANT SELECT ON TABLE asset_prod.gold.investment_accounts TO `xieming.li@databricks.com`;
-- Config groups hera-adbk-investments-read[-unmasked] are not created here, so all
-- target grants surface as ADD. To also exercise grant REMOVE, create those account
-- groups (SCIM/account admin) and GRANT a subset to them.
