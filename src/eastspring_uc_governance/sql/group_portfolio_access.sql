-- group_portfolio_access (gpa) — the group -> portfolio_code allow-list the dynamic RLS views
-- semi-join against. Framework-owned; DERIVED from the customer-owned entitlements tables.
--
-- A group sees a portfolio_code iff, on EVERY axis (region, team), it is '*' (unrestricted) OR the
-- code carries the group's value. The cross-axis INNER JOIN is the intersection and fails closed:
-- a group missing an axis row reaches zero codes. Caller-independent by design — NO is_member() /
-- current_user() here; membership is applied later, in the view, evaluated as the caller.
--
-- Materialized view with TRIGGER ON UPDATE: auto-refreshes when the source tables change (min 1
-- minute), so customer edits to entitlements / entitlement_mapping propagate without a manual or
-- scheduled rebuild. Clustered on portfolio_code (the view semi-join key). Creating/refreshing an MV
-- requires SERVERLESS compute (a Pro/Serverless SQL warehouse, or serverless general compute on
-- DBR 18.1+) — NOT a classic cluster; the source tables should have row tracking enabled for
-- efficient incremental refresh. Axes are hardcoded region/team — a third dimension needs new SQL.
CREATE OR REPLACE MATERIALIZED VIEW `${catalog}`.`${governance_schema}`.group_portfolio_access
CLUSTER BY (portfolio_code)
TRIGGER ON UPDATE
AS
WITH reach AS (                                    -- codes each group reaches, per axis
  SELECT e.ad_group, e.type, m.portfolio_code
  FROM `${catalog}`.`${governance_schema}`.entitlements e
  JOIN `${catalog}`.`${governance_schema}`.entitlement_mapping m
    ON m.type = e.type AND (e.value = '*' OR m.value = e.value)
  WHERE e.tag = 'portfolio_scope'
)
SELECT r.ad_group, r.portfolio_code                -- keep only codes reached by BOTH axes
FROM      (SELECT ad_group, portfolio_code FROM reach WHERE type = 'region') r
JOIN      (SELECT ad_group, portfolio_code FROM reach WHERE type = 'team')   t
  ON r.ad_group = t.ad_group AND r.portfolio_code = t.portfolio_code
GROUP BY r.ad_group, r.portfolio_code;             -- dedup fan-out (= DISTINCT)
