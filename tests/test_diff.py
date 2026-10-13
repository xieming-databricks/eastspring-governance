"""Tests for the PySpark DataFrame diff.

Requires pyspark: skipped where it isn't installed (e.g. local Python 3.14), run on
any machine/cluster that has it. Execution is also validated against the live workspace.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from eastspring_uc_governance.reconcile import diff  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("diff-tests")
         .config("spark.ui.enabled", "false").getOrCreate())
    yield s
    s.stop()


_GRANT = "principal string, privilege_type string, securable_type string, securable_fqn string"
_TAG = "securable_type string, securable_fqn string, tag_key string, tag_value string"
_POLICY = ("scope string, policy_name string, policy_type string, function string, "
           "except_principals string")


def _keyset(df, *cols):
    return {tuple(r[c] for c in cols) for r in df.collect()}


# ---- grants: full-sync + protected set ----------------------------------

def test_grants_full_sync_removes_any_unprotected_principal(spark):
    """Grant full-sync adds missing target grants and revokes every undeclared live grant, whichever
    principal holds it.

    Expected: ADD c.s.t, REMOVE c.s.old (the managed group's stale grant) and REMOVE c.s.x (another
    principal's grant).
    """
    target = spark.createDataFrame([("grp", "SELECT", "table", "c.s.t")], _GRANT)
    live = spark.createDataFrame([
        ("grp", "SELECT", "table", "c.s.old"),     # managed group, not in target -> REMOVE
        ("other", "SELECT", "table", "c.s.x"),     # unmanaged principal -> now REMOVE (full-sync)
    ], _GRANT)
    out = diff.diff_grants(target, live)
    assert _keyset(out, "change_type", "securable_fqn") == {
        ("ADD", "c.s.t"), ("REMOVE", "c.s.old"), ("REMOVE", "c.s.x")}


def test_grants_protected_principal_and_schema(spark):
    """Live grants held by a protected principal or sitting in a protected schema are never proposed
    for removal.

    Expected: the only REMOVE is c.s.b; dep@x's grant and the information_schema grant are kept.
    """
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
    """Identical target and live grants produce no delta.

    Expected: an empty delta (0 rows).
    """
    row = [("grp", "SELECT", "table", "c.s.t")]
    out = diff.diff_grants(spark.createDataFrame(row, _GRANT), spark.createDataFrame(row, _GRANT))
    assert out.count() == 0


# ---- grants: managed scope (skip_base_grants) ----------------------------

def test_grants_managed_schemas_leave_base_grants_alone(spark):
    """With managed_schemas set (skip_base_grants), grants are full-synced only inside the serving
    schema: base-schema grants are ignored on both sides and catalog-level grants are add-only.

    Expected: exactly ADD viewers on c.serving, ADD viewers USE_CATALOG on c, and REMOVE stale on
    c.serving.v; the admins' catalog grant and base-table grant are kept, and the admins' target
    base grant is not added.
    """
    # skip_base_grants: full-sync only inside the serving schema(s); base-table grants elsewhere
    # are neither added nor revoked, and catalog-level grants are add-only.
    target = spark.createDataFrame([
        ("viewers", "SELECT", "schema", "c.serving"),       # managed -> ADD
        ("viewers", "USE_CATALOG", "catalog", "c"),         # catalog-level, missing -> ADD
        ("admins", "SELECT", "table", "c.gold.t"),          # base schema, unmanaged -> ignored
    ], _GRANT)
    live = spark.createDataFrame([
        ("stale", "SELECT", "view", "c.serving.v"),         # managed, undeclared -> REMOVE
        ("admins", "USE_CATALOG", "catalog", "c"),          # catalog-level, undeclared -> KEPT
        ("admins", "MODIFY", "table", "c.gold.t"),          # base grant -> KEPT (not revoked)
    ], _GRANT)
    out = diff.diff_grants(target, live, target_catalog="c", managed_schemas=["serving"])
    assert _keyset(out, "change_type", "principal", "securable_fqn") == {
        ("ADD", "viewers", "c.serving"),
        ("ADD", "viewers", "c"),
        ("REMOVE", "stale", "c.serving.v"),
    }


def test_grants_managed_schemas_none_is_whole_catalog(spark):
    """With managed_schemas None (base grants managed), the whole catalog is full-synced, so an
    undeclared base-table grant is revoked.

    Expected: the delta includes REMOVE c.gold.t, which shows the managed scope is what protects
    base grants.
    """
    # default (base grants managed): the same live base grant IS revoked — proves the scope matters.
    target = spark.createDataFrame([("viewers", "SELECT", "schema", "c.serving")], _GRANT)
    live = spark.createDataFrame([("admins", "MODIFY", "table", "c.gold.t")], _GRANT)
    out = diff.diff_grants(target, live, target_catalog="c", managed_schemas=None)
    assert ("REMOVE", "c.gold.t") in _keyset(out, "change_type", "securable_fqn")


def test_grants_managed_schemas_empty_only_adds_catalog_grants(spark):
    """With managed_schemas empty (skip_base_grants and no serving schemas), nothing below catalog
    level is managed and catalog grants are add-only.

    Expected: only ADD c (the missing USE_CATALOG); the live table grant and the other catalog grant
    are untouched.
    """
    # skip_base_grants with no serving schemas: nothing below catalog level is managed.
    target = spark.createDataFrame([("viewers", "USE_CATALOG", "catalog", "c")], _GRANT)
    live = spark.createDataFrame([("x", "SELECT", "table", "c.any.t"),
                                  ("y", "USE_CATALOG", "catalog", "c")], _GRANT)
    out = diff.diff_grants(target, live, target_catalog="c", managed_schemas=[])
    assert _keyset(out, "change_type", "securable_fqn") == {("ADD", "c")}


def test_diff_all_passes_managed_scope_to_grants_only(spark):
    """diff_all applies managed_grant_schemas to the grant diff only; tags keep the whole-catalog
    scope.

    Expected: no grant delta (the base-table grant is untouched) while the undeclared acm_ column
    tag on the base table is still REMOVEd.
    """
    empty_g = spark.createDataFrame([], _GRANT)
    live_g = spark.createDataFrame([("admins", "MODIFY", "table", "c.gold.t")], _GRANT)
    tags = spark.createDataFrame([], _TAG)
    live_tags = spark.createDataFrame([("column", "c.gold.t.col", "acm_pii_type", "EMAIL")], _TAG)
    pol = spark.createDataFrame([], _POLICY)
    out = diff.diff_all({"grants": empty_g, "tags": tags, "policies": pol},
                        {"grants": live_g, "tags": live_tags, "policies": pol},
                        target_catalog="c", managed_grant_schemas=["serving"])
    assert out["grants"].count() == 0                                  # base grant untouched
    assert _keyset(out["tags"], "change_type") == {("REMOVE",)}        # tags still whole-catalog


# ---- tags: ADD / REMOVE / CHANGE, acm_ only -----------------------------

def test_tags_add_remove_change(spark):
    """The tag diff adds, changes and removes acm_-prefixed tags and ignores tags outside that
    namespace.

    Expected: ADD acm_pii_type, CHANGE acm_business_function and REMOVE acm_rls; the non-acm
    cost_center tag never appears.
    """
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
    """A mask policy whose function or EXCEPT list differs from live is a CHANGE, and policies
    outside the acm_policy_ namespace are ignored.

    Expected: exactly CHANGE acm_policy_mask_x__h1 (function) and CHANGE acm_policy_mask_y__h2
    (EXCEPT list); someones_policy is untouched.
    """
    target = spark.createDataFrame([
        ("c.s", "acm_policy_mask_x__h1", "column_mask", "c.g.new_fn", "etl-a"),   # fn CHANGE
        ("c.s", "acm_policy_mask_y__h2", "column_mask", "c.g.fn", "etl-a,etl-b"), # EXCEPT CHANGE
    ], _POLICY)
    live = spark.createDataFrame([
        ("c.s", "acm_policy_mask_x__h1", "column_mask", "c.g.old_fn", "etl-a"),   # function differs
        ("c.s", "acm_policy_mask_y__h2", "column_mask", "c.g.fn", "etl-a"),       # except differs
        ("c.s", "someones_policy", "column_mask", "c.g.z", ""),                   # non-framework -> ignored
    ], _POLICY)
    out = {(r["change_type"], r["policy_name"]) for r in diff.diff_policies(target, live).collect()}
    assert out == {("CHANGE", "acm_policy_mask_x__h1"), ("CHANGE", "acm_policy_mask_y__h2")}


# ---- diff_all: out-of-catalog target rows ignored -----------------------

def test_diff_all_ignores_out_of_catalog_target(spark):
    """Target grants outside target_catalog are dropped from the diff.

    Expected: only asset_prod.gold.t appears in the grant delta; other_cat.gold.t is ignored.
    """
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
