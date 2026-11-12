-- seed_esg.sql — DEV/TEST fixture for examples/esg (target_catalog = hera-dev). Never run it in a
-- customer environment: there the catalog and base tables already exist.
--
-- Creates the catalog, the source + governance schemas and the base tables that
-- examples/esg/config/esg-dev references (via labels.yaml and the GR001 include), so the example can
-- round-trip end to end (plan -> apply -> empty plan) in an empty dev workspace. The serving
-- schemas are NOT created here: `ucgov-provision schemas` does that (see examples/esg/README.md).
--
-- NOTE ON GROUPS: the account groups the config grants to must exist BEFORE an `apply`. The
-- example reference has every external_id null, so they are created as local account groups:
--     ucgov-provision groups --reference examples/esg/reference/esg-dev.yaml --profile <workspace-profile> --apply   (workspace admin)
-- (grants to a non-existent principal fail at apply time.)
--
-- `hera-dev` contains a hyphen, so it MUST be backtick-quoted everywhere.
-- Tables are managed Delta tables (empty) — the metastore/catalog needs a managed storage
-- location; if yours doesn't, add `MANAGED LOCATION '...'` to the CREATE CATALOG/SCHEMA.
-- Representative columns (author / portfolio_code) match the Asset Classification tab so the
-- fixture also supports a later ABAC (masking/RLS) round-trip.

-- ---- catalog + schemas --------------------------------------------------
CREATE CATALOG IF NOT EXISTS `hera-dev`;
CREATE SCHEMA  IF NOT EXISTS `hera-dev`.gold;
CREATE SCHEMA  IF NOT EXISTS `hera-dev`.silver;
CREATE SCHEMA  IF NOT EXISTS `hera-dev`.governance;

-- ---- gold ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `hera-dev`.gold.esg_engagement (        -- data_product: investment_research
  engagement_id STRING,
  author        STRING,                                            -- pii_type: FULL_NAME (future ABAC)
  esg_theme     STRING);

CREATE TABLE IF NOT EXISTS `hera-dev`.gold.esg_waci (              -- data_product: investment_esg
  portfolio_code STRING,                                           -- pii_type: identifier / rls: portfolio_scope
  waci           DOUBLE);

CREATE TABLE IF NOT EXISTS `hera-dev`.gold.example_restricted_esg ( -- GR001 EXCLUDE target
  portfolio_code  STRING,
  restricted_flag BOOLEAN);

-- ---- silver -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `hera-dev`.silver.fact_esg_engagement ( -- data_product: investment_research
  engagement_id STRING,
  author        STRING,                                            -- pii_type: FULL_NAME
  score         DOUBLE);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.fact_portfolio_position ( -- data_product: portfolio_position
  portfolio_code STRING,                                           -- pii_type: identifier / rls: portfolio_scope
  position_value DOUBLE);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.dim_portfolio (       -- data_product: mdm_portfolio_structure
  portfolio_code STRING,
  portfolio_name STRING);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.dim_fund (            -- data_product: mdm_portfolio_structure
  portfolio_code STRING,
  fund_name      STRING);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.dim_mandate (         -- data_product: mdm_portfolio_structure
  portfolio_code STRING,
  mandate_name   STRING);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.dim_share_class (     -- data_product: mdm_portfolio_structure
  share_class_id STRING,
  portfolio_code STRING);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.fact_portfolio_transaction ( -- data_product: portfolio_transaction
  portfolio_code STRING,
  txn_amount     DOUBLE);

CREATE TABLE IF NOT EXISTS `hera-dev`.silver.fact_price (          -- data_product: market_data
  security_id STRING,
  price       DOUBLE);

-- ---- governance ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS `hera-dev`.governance.example_esg_helper ( -- GR001 INCLUDE target
  k STRING,
  v STRING);

-- NOTE: data_product `mdm_security` has no table in the Access Matrix's Tables tab, so its
-- grant rules (GR006/013/020/027/034) select nothing — intentional, nothing to seed here.


-- Seed data for the empty esg-dev test tables in `hera-dev` (sandbox adb-7405609945564168).
-- All 9 tables below had 0 rows on 2026-11-10; portfolio codes match silver.dim_portfolio + the entitlement mapping.

-- FULL_NAME mask (gold policy) -> masked readers see "J***", "J***", "A***"
INSERT INTO `hera-dev`.gold.esg_engagement (engagement_id, author, esg_theme) VALUES
  ('E1', 'Jane Doe',   'Climate'),
  ('E2', 'John Smith', 'Governance'),
  ('E3', 'Alice Tan',  'Social');

-- Gap check: author is tagged FULL_NAME, but the FULL_NAME policy is scoped to gold -> expected raw for everyone
INSERT INTO `hera-dev`.silver.fact_esg_engagement (engagement_id, score, author) VALUES
  ('E1', 0.82, 'Jane Doe'),
  ('E2', 0.64, 'John Smith'),
  ('E3', 0.71, 'Alice Tan');

-- Gap check: portfolio_code is tagged identifier, but the identifier policy is scoped to silver -> expected raw
INSERT INTO `hera-dev`.gold.esg_waci (portfolio_code, waci) VALUES
  ('PF_EQ_1', 120.5), ('PF_EQ_2', 98.2), ('PF_FI_1', 45.0), ('PF_OTHER', 210.9);

-- identifier mask (silver policy), no RLS -> masked readers see sha2(portfolio_code)
INSERT INTO `hera-dev`.silver.dim_fund (portfolio_code, fund_name) VALUES
  ('PF_EQ_1', 'Asia Equity Fund'), ('PF_EQ_2', 'Global Equity Fund'),
  ('PF_FI_1', 'Asia Bond Fund'),   ('PF_OTHER', 'Americas Fund');
INSERT INTO `hera-dev`.silver.dim_mandate (portfolio_code, mandate_name) VALUES
  ('PF_EQ_1', 'Equity Mandate A'), ('PF_EQ_2', 'Equity Mandate B'),
  ('PF_FI_1', 'Fixed Income Mandate'), ('PF_OTHER', 'Other Mandate');

-- Controls: nothing tagged -> everyone with view access sees raw values, all rows
INSERT INTO `hera-dev`.silver.dim_share_class (portfolio_code, share_class_id) VALUES
  ('PF_EQ_1', 'SC-A'), ('PF_EQ_2', 'SC-B'), ('PF_FI_1', 'SC-C'), ('PF_OTHER', 'SC-D');
INSERT INTO `hera-dev`.silver.fact_price (security_id, price) VALUES
  ('SEC-001', 101.25), ('SEC-002', 55.10), ('SEC-003', 9.87);

-- GR001 include (admin can read) / exclude (admin must be denied)
INSERT INTO `hera-dev`.governance.example_esg_helper (k, v) VALUES ('helper_key', 'helper_value');
INSERT INTO `hera-dev`.gold.example_restricted_esg (portfolio_code, restricted_flag) VALUES ('PF_EQ_1', true);
