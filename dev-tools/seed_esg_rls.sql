-- seed_esg_rls.sql — DEV/TEST fixture for view-based RLS (config/esg, target_catalog = hera-dev).
--
-- In PRODUCTION these two tables are CUSTOMER-OWNED (the customer authors + maintains the group
-- scopes and the attribute->portfolio_code mapping, e.g. from the workbook tabs). The framework
-- only READS them (to derive group_portfolio_access) — it never manages their DDL/data in prod.
-- This script exists so a fresh dev workspace has something to derive gpa from and to prove the
-- RLS views filter correctly. Run it (SQL editor / warehouse) BEFORE apply_functions.
--
-- `hera-dev` contains a hyphen, so it MUST be backtick-quoted everywhere.

-- ---- source table 1: group -> per-axis scope ('*' = unrestricted on that axis) --------
CREATE TABLE IF NOT EXISTS `hera-dev`.governance.entitlements (
  ad_group STRING,          -- account group name (matches is_account_group_member())
  tag      STRING,          -- scope tag; only 'portfolio_scope' is used today
  type     STRING,          -- axis: 'region' | 'team'
  value    STRING)          -- axis value, or '*' for unrestricted
TBLPROPERTIES (delta.enableRowTracking = true);   -- lets the gpa MV refresh incrementally on change
                                                   -- (prod: customer tables should match)

DELETE FROM `hera-dev`.governance.entitlements;
INSERT INTO `hera-dev`.governance.entitlements VALUES
  ('GSGEIS-hera-all-esg-read-masked',    'portfolio_scope', 'region', '*'),
  ('GSGEIS-hera-all-esg-read-masked',    'portfolio_scope', 'team',   '*'),
  ('GSGEIS-hera-all-esg-read-unmasked',  'portfolio_scope', 'region', '*'),
  ('GSGEIS-hera-all-esg-read-unmasked',  'portfolio_scope', 'team',   '*'),
  ('GSGEIS-hera-sg-eq-read-masked',      'portfolio_scope', 'region', 'EISG'),
  ('GSGEIS-hera-sg-eq-read-masked',      'portfolio_scope', 'team',   'EQ'),
  ('GSGEIS-hera-sg-esg-read-masked',     'portfolio_scope', 'region', 'EISG'),
  ('GSGEIS-hera-sg-esg-read-masked',     'portfolio_scope', 'team',   '*'),
  ('GSGEIS-hera-sg-fi-read-masked',      'portfolio_scope', 'region', 'EISG'),
  ('GSGEIS-hera-sg-fi-read-masked',      'portfolio_scope', 'team',   'FI');

-- ---- source table 2: (axis attribute) -> portfolio_codes that carry it ------------------
CREATE TABLE IF NOT EXISTS `hera-dev`.governance.entitlement_mapping (
  type           STRING,    -- 'region' | 'team'
  value          STRING,    -- axis value
  portfolio_code STRING)
TBLPROPERTIES (delta.enableRowTracking = true);   -- lets the gpa MV refresh incrementally on change

DELETE FROM `hera-dev`.governance.entitlement_mapping;
INSERT INTO `hera-dev`.governance.entitlement_mapping VALUES
  ('region', 'EISG', 'PF_EQ_1'),
  ('region', 'EISG', 'PF_EQ_2'),
  ('region', 'EISG', 'PF_FI_1'),
  ('region', 'AMER', 'PF_OTHER'),
  ('team',   'EQ',   'PF_EQ_1'),
  ('team',   'EQ',   'PF_EQ_2'),
  ('team',   'FI',   'PF_FI_1'),
  ('team',   'OTHER','PF_OTHER');

-- ---- sample fact/dim rows so the RLS views have something to filter (dev only) ----------
DELETE FROM `hera-dev`.silver.fact_portfolio_position;
INSERT INTO `hera-dev`.silver.fact_portfolio_position VALUES
  ('PF_EQ_1', 100.0), ('PF_EQ_2', 200.0), ('PF_FI_1', 300.0), ('PF_OTHER', 400.0);
DELETE FROM `hera-dev`.silver.fact_portfolio_transaction;
INSERT INTO `hera-dev`.silver.fact_portfolio_transaction VALUES
  ('PF_EQ_1', 10.0), ('PF_EQ_2', 20.0), ('PF_FI_1', 30.0), ('PF_OTHER', 40.0);
DELETE FROM `hera-dev`.silver.dim_portfolio;
INSERT INTO `hera-dev`.silver.dim_portfolio VALUES
  ('PF_EQ_1', 'Equity One'), ('PF_EQ_2', 'Equity Two'), ('PF_FI_1', 'Fixed Income One'),
  ('PF_OTHER', 'Other');

-- ---- data-quality checks (fail-closed protection) — each MUST return 0 rows -------------
-- (1) every (ad_group, tag) must have BOTH axes; a missing axis silently grants zero codes.
SELECT ad_group, tag, 'missing an axis (region/team)' AS problem
  FROM `hera-dev`.governance.entitlements
 WHERE tag = 'portfolio_scope'
 GROUP BY ad_group, tag
HAVING count(DISTINCT type) < 2;
-- (2) every referenced portfolio_code must map on BOTH axes, else it's dropped from every group.
SELECT portfolio_code, 'maps on <2 axes' AS problem
  FROM `hera-dev`.governance.entitlement_mapping
 GROUP BY portfolio_code
HAVING count(DISTINCT type) < 2;
