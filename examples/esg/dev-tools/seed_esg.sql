-- seed_esg.sql — DEV/TEST fixture for examples/esg (target_catalog = hera-dev). Never run it in a
-- customer environment: there the catalog and base tables already exist.
--
-- Creates the catalog, the source + governance schemas and the base tables that
-- examples/esg/config/esg references (via labels.yaml and the GR001 include), so the example can
-- round-trip end to end (plan -> apply -> empty plan) in an empty dev workspace. The serving
-- schemas are NOT created here: `ucgov-provision schemas` does that (see examples/esg/README.md).
--
-- NOTE ON GROUPS: the account groups the config grants to must exist BEFORE an `apply`. The
-- example reference has every external_id null, so they are created as local account groups:
--     ucgov-provision groups --reference examples/esg/reference/esg.yaml --profile <workspace-profile> --apply   (workspace admin)
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
