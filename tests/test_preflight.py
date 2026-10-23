"""Tests for the preflight existence check."""
from __future__ import annotations

from eastspring_uc_governance.compile.model import RefObject
from eastspring_uc_governance.reconcile import preflight


def test_missing_object_and_function_warn():
    """Preflight flags referenced objects (a table and a mask function) that do not exist live,
    while an existing table passes.

    Expected: exactly two warnings: table c.s.missing and function c.g.absent_fn; the present table
    c.s.present is not flagged.
    """
    refs = [
        RefObject("table", "c.s.present", "tag"),
        RefObject("table", "c.s.missing", "grant_rules/0"),
        RefObject("function", "c.g.absent_fn", "policy.function"),
    ]
    existing = {("table", "c.s.present")}
    warns = preflight.check_existence(refs, existing)
    missing = {(w["object_type"], w["fqn"]) for w in warns}
    assert missing == {("table", "c.s.missing"), ("function", "c.g.absent_fn")}


def test_all_present_no_warnings():
    """Preflight reports nothing when every referenced catalog and schema exists live.

    Expected: an empty warning list.
    """
    refs = [RefObject("catalog", "c", "x"), RefObject("schema", "c.s", "x")]
    existing = {("catalog", "c"), ("schema", "c.s")}
    assert preflight.check_existence(refs, existing) == []


def test_view_table_leniency():
    """Preflight treats view and table as interchangeable, since config may call an object a view
    while live state lists it as a table (or vice versa).

    Expected: an empty warning list for both cross-typed references.
    """
    # config referenced a view; live surfaced it under 'table' (or vice versa) -> no warning
    refs = [RefObject("view", "c.s.v", "grant_rules/0"), RefObject("table", "c.s.t", "tag")]
    existing = {("table", "c.s.v"), ("view", "c.s.t")}
    assert preflight.check_existence(refs, existing) == []


def test_dedup_repeated_refs():
    """Preflight de-duplicates a missing object that is referenced twice from different places.

    Expected: exactly one warning for c.s.missing.
    """
    refs = [RefObject("table", "c.s.missing", "a"), RefObject("table", "c.s.missing", "b")]
    warns = preflight.check_existence(refs, set())
    assert len(warns) == 1


# ---- RLS allow-list table (rls_entitlement_table) ---------------------------

_RLS = "hera-dev.governance.group_portfolio_access"


def test_rls_table_fits():
    """The allow-list exists with the group column and every rls_column the views use; column
    names compare case-insensitively.

    Expected: no warnings.
    """
    assert preflight.check_rls_table(_RLS, {"AD_GROUP", "portfolio_code"}, group_column="ad_group",
                                     rls_columns={"portfolio_code"}) == []


def test_rls_table_missing_or_unset():
    """A missing allow-list table, or none in the reference, is reported once — every RLS view
    would fail on it.

    Expected: one rls_table_missing warning each; the unset case names the rls_column(s).
    """
    missing = preflight.check_rls_table(_RLS, None, group_column="ad_group", rls_columns={"portfolio_code"})
    assert [(w["kind"], w["fqn"]) for w in missing] == [("rls_table_missing", _RLS)]
    unset = preflight.check_rls_table(None, None, group_column="ad_group", rls_columns={"portfolio_code"})
    assert unset[0]["kind"] == "rls_table_missing" and "portfolio_code" in unset[0]["reason"]


def test_rls_table_missing_columns():
    """Each column the semi-join needs but the table lacks is its own warning: the group column
    first, then the rls_columns.

    Expected: rls_column_missing for ad_group and fund_code, in that order.
    """
    out = preflight.check_rls_table(_RLS, {"portfolio_code"}, group_column="ad_group",
                                    rls_columns={"portfolio_code", "fund_code"})
    assert [(w["kind"], w["fqn"]) for w in out] == [("rls_column_missing", f"{_RLS}.ad_group"),
                                                    ("rls_column_missing", f"{_RLS}.fund_code")]


def test_rls_table_not_needed_without_rls_views():
    """No view uses an rls_column -> nothing to check, even with no table at all.

    Expected: no warnings.
    """
    assert preflight.check_rls_table(None, None, group_column="ad_group", rls_columns=set()) == []


# ---- serving schemas: provisioned, never created by reconcile ----------------

def test_view_schemas_all_exist():
    """Every schema the views go into exists -> nothing to report.

    Expected: no warnings.
    """
    assert preflight.check_view_schemas(["c.dm_esg", "c.dm_esg", "c.dm_x"], {"c.dm_esg", "c.dm_x", "c.gold"}) == []


def test_missing_view_schema_is_reported_once():
    """A view schema that doesn't exist (not provisioned yet, or a workbook typo) is reported once,
    pointing at ucgov-provision schemas — reconcile never creates it.

    Expected: one view_schema_missing warning for c.dm_typo naming ucgov-provision schemas.
    """
    out = preflight.check_view_schemas(["c.dm_esg", "c.dm_typo", "c.dm_typo"], {"c.dm_esg"})
    assert [(w["kind"], w["fqn"]) for w in out] == [("view_schema_missing", "c.dm_typo")]
    assert "ucgov-provision schemas" in out[0]["reason"]
