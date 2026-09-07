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
    assert statement.tag_statement(_t("REMOVE", "table", "c.s.t", "acm_rls")) == (
        "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('acm_rls')")


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


# ---- dispatch / ordering ------------------------------------------------

def test_policies_are_detect_only():
    assert statement.statements_for("policies", [{"scope": "c.s", "policy_name": "acm_policy_x",
                                                  "change_type": "ADD"}]) == []


def test_statements_for_orders_add_before_remove():
    rows = [
        _g("REMOVE", "g", "SELECT", "table", "c.s.old"),
        _g("ADD", "g", "SELECT", "table", "c.s.new"),
    ]
    cts = [row["change_type"] for row, _ in statement.statements_for("grants", rows)]
    assert cts == ["ADD", "REMOVE"]


def test_plan_all_flattens_grants_then_tags_skips_policies():
    deltas = {
        "grants": [_g("ADD", "g", "SELECT", "table", "c.s.t")],
        "tags": [_t("ADD", "table", "c.s.t", "acm_x", "v")],
        "policies": [{"scope": "c.s", "policy_name": "acm_policy_x", "change_type": "ADD"}],
    }
    planned = statement.plan_all(deltas)
    dims = [dim for dim, _row, _stmt in planned]
    assert dims == ["grants", "tags"]                 # policies skipped, grants before tags
    assert all(isinstance(stmt, str) for _d, _r, stmt in planned)


def test_unknown_dimension_raises():
    with pytest.raises(statement.ApplyError):
        statement.statements_for("widgets", [])
