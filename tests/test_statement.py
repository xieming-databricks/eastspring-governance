"""Tests for apply DDL generation (pure string generation, no Spark)."""
from __future__ import annotations

import pytest

from framework.apply import statement


def _g(change_type, principal, priv, stype, fqn):
    return {"principal": principal, "privilege_type": priv, "securable_type": stype,
            "securable_fqn": fqn, "change_type": change_type}


def _t(change_type, stype, fqn, key, value=None):
    return {"securable_type": stype, "securable_fqn": fqn, "tag_key": key,
            "tag_value": value, "change_type": change_type}


# ---- grants: ADD -> GRANT, REMOVE -> REVOKE, all securable kinds --------

def test_grant_add_table():
    assert statement.grant_statement(_g("ADD", "hera-adbk-investments-read", "SELECT", "table",
                                        "asset_prod.gold.investment_accounts")) == (
        "GRANT SELECT ON TABLE `asset_prod`.`gold`.`investment_accounts` "
        "TO `hera-adbk-investments-read`")


def test_grant_remove_table():
    assert statement.grant_statement(_g("REMOVE", "analyst", "SELECT", "table", "c.s.t")) == (
        "REVOKE SELECT ON TABLE `c`.`s`.`t` FROM `analyst`")


def test_grant_securable_keywords():
    assert "ON CATALOG `asset_prod`" in statement.grant_statement(
        _g("ADD", "g", "USE_CATALOG", "catalog", "asset_prod"))
    assert "ON SCHEMA `asset_prod`.`gold`" in statement.grant_statement(
        _g("ADD", "g", "USE_SCHEMA", "schema", "asset_prod.gold"))
    assert "ON FUNCTION `c`.`s`.`f`" in statement.grant_statement(
        _g("ADD", "g", "EXECUTE", "function", "c.s.f"))
    assert "ON VOLUME `c`.`s`.`v`" in statement.grant_statement(
        _g("ADD", "g", "READ_VOLUME", "volume", "c.s.v"))


# ---- privilege form: underscore passthrough (UC accepts it directly) ----

@pytest.mark.parametrize("priv", [
    "SELECT", "MODIFY", "EXECUTE", "MANAGE", "USE_CATALOG", "USE_SCHEMA",
    "READ_VOLUME", "CREATE_TABLE", "CREATE_FUNCTION", "CREATE_VOLUME",
])
def test_privilege_passthrough_underscore_form(priv):
    # No conversion: the information_schema underscore form is emitted verbatim
    # (verified that UC's GRANT parser accepts USE_CATALOG etc.).
    assert statement.grant_statement(_g("ADD", "g", priv, "catalog", "c")) == (
        f"GRANT {priv} ON CATALOG `c` TO `g`")


# ---- tags: SET / UNSET / column split / CHANGE --------------------------

def test_tag_set_table():
    assert statement.tag_statement(_t("ADD", "table", "asset_prod.gold.investment_accounts",
                                      "acm_business_function", "investments")) == (
        "ALTER TABLE `asset_prod`.`gold`.`investment_accounts` "
        "SET TAGS ('acm_business_function' = 'investments')")


def test_tag_change_is_set():
    assert statement.tag_statement(_t("CHANGE", "table", "c.s.t", "acm_business_function",
                                      "compliance")).startswith("ALTER TABLE `c`.`s`.`t` SET TAGS")


def test_tag_unset_table():
    assert statement.tag_statement(_t("REMOVE", "table", "c.s.t", "acm_pii_type")) == (
        "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('acm_pii_type')")


def test_tag_set_column_splits_4part_fqn():
    assert statement.tag_statement(_t("ADD", "column",
                                      "asset_prod.gold.investment_accounts.client_name",
                                      "acm_pii_type", "FULL_NAME")) == (
        "ALTER TABLE `asset_prod`.`gold`.`investment_accounts` "
        "ALTER COLUMN `client_name` SET TAGS ('acm_pii_type' = 'FULL_NAME')")


def test_tag_column_bad_fqn_raises():
    with pytest.raises(statement.ApplyError):
        statement.tag_statement(_t("ADD", "column", "c.s.t", "acm_pii_type", "X"))  # only 3 parts


def test_tag_value_single_quote_escaped():
    assert statement.tag_statement(_t("ADD", "table", "c.s.t", "acm_x", "O'Brien")) == (
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('acm_x' = 'O''Brien')")


# ---- principal quoting --------------------------------------------------

def test_principal_quoting_group_and_email():
    assert statement.grant_statement(_g("ADD", "hera-adbk-x", "SELECT", "table", "c.s.t")).endswith(
        "TO `hera-adbk-x`")
    assert statement.grant_statement(_g("ADD", "a@b.com", "SELECT", "table", "c.s.t")).endswith(
        "TO `a@b.com`")


# ---- ABAC column-mask policies ------------------------------------------

def _pdef(scope="hera-dev.gold", name="acm_policy_mask_full_name__habc",
          fn="hera-dev.governance.acm_mask_full_name", col_val="FULL_NAME",
          tkey=None, tval=None, exempt=""):
    return {"scope": scope, "policy_name": name, "function": fn,
            "column_tag_key": "acm_pii_type", "column_tag_value": col_val,
            "table_tag_key": tkey, "table_tag_value": tval, "except_principals": exempt}


def _prow(change_type, scope="hera-dev.gold", name="acm_policy_mask_full_name__habc"):
    return {"scope": scope, "policy_name": name, "change_type": change_type}


def test_policy_create_or_replace_basic():
    sql = statement.policy_statement(_prow("ADD"), _pdef())
    assert sql.startswith("CREATE OR REPLACE POLICY acm_policy_mask_full_name__habc "
                          "ON SCHEMA `hera-dev`.`gold`")
    assert "COLUMN MASK `hera-dev`.`governance`.`acm_mask_full_name`" in sql
    assert "TO `account users`" in sql
    assert "FOR TABLES" in sql
    assert "MATCH COLUMNS has_tag_value('acm_pii_type', 'FULL_NAME') AS c" in sql
    assert sql.rstrip().endswith("ON COLUMN c")
    assert "EXCEPT" not in sql            # no exempt principals -> no EXCEPT
    assert "WHEN" not in sql              # no table_matching_tags -> no WHEN


def test_policy_create_with_except_and_when():
    sql = statement.policy_statement(
        _prow("CHANGE"),
        _pdef(exempt="hera-etl-ingest,sp-pipelines", tkey="acm_business_function", tval="investments"))
    assert "EXCEPT `hera-etl-ingest`, `sp-pipelines`" in sql
    assert "WHEN has_tag_value('acm_business_function', 'investments')" in sql


def test_policy_change_uses_create_or_replace():
    assert statement.policy_statement(_prow("CHANGE"), _pdef()).startswith("CREATE OR REPLACE POLICY")


def test_policy_remove_is_drop():
    sql = statement.policy_statement(_prow("REMOVE", scope="hera-dev.gold.t", name="acm_policy_z"), None)
    assert sql == "DROP POLICY acm_policy_z ON TABLE `hera-dev`.`gold`.`t`"


def test_policy_scope_keyword_by_depth():
    assert " ON CATALOG " in statement.policy_statement(_prow("ADD", scope="cat"), _pdef(scope="cat"))
    assert " ON SCHEMA " in statement.policy_statement(_prow("ADD", scope="cat.sch"), _pdef(scope="cat.sch"))
    assert " ON TABLE " in statement.policy_statement(_prow("ADD", scope="cat.sch.t"), _pdef(scope="cat.sch.t"))


def test_policy_add_without_def_raises():
    with pytest.raises(statement.ApplyError):
        statement.policy_statement(_prow("ADD"), None)


# ---- dynamic views (view-based RLS) -------------------------------------

def test_view_statement_pass_through():
    sql = statement.view_statement(
        {"view": "hera-dev.dm_esg.dim_fund", "source": "hera-dev.silver.dim_fund"},
        entitlement_table="hera-dev.governance.group_portfolio_access")
    assert sql == ("CREATE OR REPLACE VIEW `hera-dev`.`dm_esg`.`dim_fund` AS "
                   "SELECT * FROM `hera-dev`.`silver`.`dim_fund`")


def test_view_statement_rls_semijoin():
    sql = statement.view_statement(
        {"view": "hera-dev.dm_esg.fact_portfolio_position",
         "source": "hera-dev.silver.fact_portfolio_position", "rls_column": "portfolio_code"},
        entitlement_table="hera-dev.governance.group_portfolio_access")
    assert sql.startswith("CREATE OR REPLACE VIEW `hera-dev`.`dm_esg`.`fact_portfolio_position` AS")
    assert "SELECT f.* FROM `hera-dev`.`silver`.`fact_portfolio_position` f" in sql
    assert "WHERE f.`portfolio_code` IN (" in sql
    assert "SELECT `portfolio_code` FROM `hera-dev`.`governance`.`group_portfolio_access`" in sql
    assert "WHERE is_account_group_member(`ad_group`))" in sql


def test_view_statement_custom_group_col():
    sql = statement.view_statement({"view": "c.s.v", "source": "c.s.t", "rls_column": "pc"},
                                   entitlement_table="c.g.gpa", group_col="grp")
    assert "is_account_group_member(`grp`)" in sql


def test_drop_view_statement():
    assert statement.drop_view_statement("hera-dev.dm_esg.dim_fund") == \
        "DROP VIEW IF EXISTS `hera-dev`.`dm_esg`.`dim_fund`"


# ---- dispatch / ordering ------------------------------------------------

def test_statements_for_policies_looks_up_defs():
    rows = [_prow("ADD"), _prow("REMOVE", name="acm_policy_gone")]
    defs = {("hera-dev.gold", "acm_policy_mask_full_name__habc"): _pdef()}
    out = statement.statements_for("policies", rows, policy_defs=defs)
    sqls = {r["change_type"]: s for r, s in out}
    assert sqls["ADD"].startswith("CREATE OR REPLACE POLICY")
    assert sqls["REMOVE"].startswith("DROP POLICY acm_policy_gone")


def test_statements_for_orders_add_before_remove():
    rows = [
        _g("REMOVE", "g", "SELECT", "table", "c.s.old"),
        _g("ADD", "g", "SELECT", "table", "c.s.new"),
    ]
    cts = [row["change_type"] for row, _ in statement.statements_for("grants", rows)]
    assert cts == ["ADD", "REMOVE"]


def test_plan_all_includes_policies():
    deltas = {
        "grants": [_g("ADD", "g", "SELECT", "table", "c.s.t")],
        "tags": [_t("ADD", "table", "c.s.t", "acm_x", "v")],
        "policies": [_prow("ADD")],
    }
    defs = {("hera-dev.gold", "acm_policy_mask_full_name__habc"): _pdef()}
    planned = statement.plan_all(deltas, policy_defs=defs)
    dims = [dim for dim, _row, _stmt in planned]
    assert dims == ["grants", "tags", "policies"]     # grants, then tags, then policies
    assert all(isinstance(stmt, str) for _d, _r, stmt in planned)


def test_unknown_dimension_raises():
    with pytest.raises(statement.ApplyError):
        statement.statements_for("widgets", [])
