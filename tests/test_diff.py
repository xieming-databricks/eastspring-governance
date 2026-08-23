"""Tests for the PySpark DataFrame diff.

Requires pyspark: skipped where it isn't installed (e.g. local Python 3.14), run on
any machine/cluster that has it. Execution is also validated against the live workspace.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from framework.reconcile import diff  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("diff-tests")
         .config("spark.ui.enabled", "false").getOrCreate())
    yield s
    s.stop()


_GRANT = "principal string, privilege_type string, securable_type string, securable_fqn string"
_TAG = "securable_type string, securable_fqn string, tag_key string, tag_value string"
_POLICY = "scope string, policy_name string, policy_type string, function string"


def _keyset(df, *cols):
    return {tuple(r[c] for c in cols) for r in df.collect()}


# ---- grants: full-sync + protected set ----------------------------------

def test_grants_full_sync_removes_any_unprotected_principal(spark):
    target = spark.createDataFrame([("grp", "SELECT", "table", "c.s.t")], _GRANT)
    live = spark.createDataFrame([
        ("grp", "SELECT", "table", "c.s.old"),     # managed group, not in target -> REMOVE
        ("other", "SELECT", "table", "c.s.x"),     # unmanaged principal -> now REMOVE (full-sync)
    ], _GRANT)
    out = diff.diff_grants(target, live)
    assert _keyset(out, "change_type", "securable_fqn") == {
        ("ADD", "c.s.t"), ("REMOVE", "c.s.old"), ("REMOVE", "c.s.x")}


def test_grants_protected_principal_and_schema(spark):
    target = spark.createDataFrame([("grp", "SELECT", "table", "c.s.t")], _GRANT)
    live = spark.createDataFrame([
        ("dep@x", "SELECT", "table", "c.s.a"),                 # protected principal
        ("account users", "SELECT", "table", "c.information_schema.tables"),  # protected schema
        ("random", "SELECT", "table", "c.s.b"),                # unprotected -> REMOVE
    ], _GRANT)
    out = diff.diff_grants(target, live, protected_principals={"dep@x"},
                           protected_schemas={"information_schema"})
    removed = _keyset(out.filter(out.change_type == "REMOVE"), "securable_fqn")
    assert removed == {("c.s.b",)}


def test_grants_no_change_when_identical(spark):
    row = [("grp", "SELECT", "table", "c.s.t")]
    out = diff.diff_grants(spark.createDataFrame(row, _GRANT), spark.createDataFrame(row, _GRANT))
    assert out.count() == 0


# ---- tags: ADD / REMOVE / CHANGE, acm_ only -----------------------------

def test_tags_add_remove_change(spark):
    target = spark.createDataFrame([
        ("table", "c.s.t", "acm_business_function", "investments"),
        ("column", "c.s.t.col", "acm_pii_type", "FULL_NAME"),
    ], _TAG)
    live = spark.createDataFrame([
        ("table", "c.s.t", "acm_business_function", "compliance"),  # CHANGE
        ("table", "c.s.t", "acm_rls", "region"),                    # REMOVE
        ("table", "c.s.t", "cost_center", "42"),                    # non-acm -> ignored
    ], _TAG)
    out = {(r["change_type"], r["tag_key"]) for r in diff.diff_tags(target, live).collect()}
    assert ("ADD", "acm_pii_type") in out
    assert ("CHANGE", "acm_business_function") in out
    assert ("REMOVE", "acm_rls") in out
    assert all(k != "cost_center" for _, k in out)


# ---- policies: CHANGE on function, acm_policy_ only ---------------------

def test_policies_change_and_namespace(spark):
    target = spark.createDataFrame([
        ("c.s", "acm_policy_mask_x__h1", "column_mask", "c.g.new_fn"),
    ], _POLICY)
    live = spark.createDataFrame([
        ("c.s", "acm_policy_mask_x__h1", "column_mask", "c.g.old_fn"),  # CHANGE
        ("c.s", "someones_policy", "column_mask", "c.g.z"),             # non-framework -> ignored
    ], _POLICY)
    out = {(r["change_type"], r["policy_name"]) for r in diff.diff_policies(target, live).collect()}
    assert out == {("CHANGE", "acm_policy_mask_x__h1")}


# ---- diff_all: out-of-catalog target rows ignored -----------------------

def test_diff_all_ignores_out_of_catalog_target(spark):
    target = {
        "grants": spark.createDataFrame([
            ("grp", "SELECT", "table", "asset_prod.gold.t"),
            ("grp", "SELECT", "table", "other_cat.gold.t"),   # outside -> ignored
        ], _GRANT),
        "tags": spark.createDataFrame([], _TAG),
        "policies": spark.createDataFrame([], _POLICY),
    }
    live = {
        "grants": spark.createDataFrame([], _GRANT),
        "tags": spark.createDataFrame([], _TAG),
        "policies": spark.createDataFrame([], _POLICY),
    }
    deltas = diff.diff_all(target, live, target_catalog="asset_prod")
    assert _keyset(deltas["grants"], "securable_fqn") == {("asset_prod.gold.t",)}
