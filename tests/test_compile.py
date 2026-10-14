"""Tests for the pure-Python compile step (bundles + matching + expansion)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.compile import compile_config
from eastspring_uc_governance.compile.bundles import BundleError, expand_bundle

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def S(privs):
    return set(privs)


# ---- bundle expansion ---------------------------------------------------

def test_read_table_traversal_and_select():
    """The READ bundle on a table grants the traversal privileges on its catalog and schema plus
    SELECT on the table.

    Expected: exactly USE_CATALOG on a, USE_SCHEMA on a.b and SELECT on table a.b.c.
    """
    assert S(expand_bundle("READ", "table", "a.b.c")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "schema", "a.b"),
        ("SELECT", "table", "a.b.c"),
    }


def test_read_view_grants_like_table():
    """The READ bundle on a view expands the same way as on a table (UC grants views as tables).

    Expected: exactly USE_CATALOG on a, USE_SCHEMA on a.b and SELECT on a.b.v as securable type
    table.
    """
    assert S(expand_bundle("READ", "view", "a.b.v")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "schema", "a.b"),
        ("SELECT", "table", "a.b.v"),
    }


def test_read_catalog_inherits_use_schema():
    """The READ bundle on a whole catalog grants catalog-level USE_CATALOG, USE_SCHEMA and SELECT,
    which every schema and table inherits.

    Expected: exactly USE_CATALOG, USE_SCHEMA and SELECT, all on catalog a.
    """
    assert S(expand_bundle("READ", "catalog", "a")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "catalog", "a"),
        ("SELECT", "catalog", "a"),
    }


def test_read_function_and_volume():
    """The READ bundle maps to the right leaf privilege for functions and volumes.

    Expected: EXECUTE on function a.b.f and READ_VOLUME on volume a.b.vol.
    """
    assert ("EXECUTE", "function", "a.b.f") in expand_bundle("READ", "function", "a.b.f")
    assert ("READ_VOLUME", "volume", "a.b.vol") in expand_bundle("READ", "volume", "a.b.vol")


def test_write_table_adds_modify():
    """The WRITE bundle on a table grants MODIFY in addition to READ's SELECT.

    Expected: both SELECT and MODIFY on table a.b.c.
    """
    got = S(expand_bundle("WRITE", "table", "a.b.c"))
    assert ("SELECT", "table", "a.b.c") in got
    assert ("MODIFY", "table", "a.b.c") in got


def test_owner_schema_create_privileges():
    """The OWNER bundle on a schema includes the create privileges.

    Expected: CREATE_TABLE, CREATE_FUNCTION and CREATE_VOLUME on schema a.b are all present.
    """
    got = S(expand_bundle("OWNER", "schema", "a.b"))
    assert {("CREATE_TABLE", "schema", "a.b"),
            ("CREATE_FUNCTION", "schema", "a.b"),
            ("CREATE_VOLUME", "schema", "a.b")} <= got


def test_admin_catalog_manage():
    """The ADMIN bundle on a catalog includes MANAGE.

    Expected: MANAGE on catalog a.
    """
    assert ("MANAGE", "catalog", "a") in expand_bundle("ADMIN", "catalog", "a")


@pytest.mark.parametrize("bundle,obj", [("WRITE", "function"), ("WRITE", "volume"),
                                        ("OWNER", "table"), ("OWNER", "catalog")])
def test_unsupported_combo_raises(bundle, obj):
    """For each bundle/object combination with no defined meaning (WRITE on a function or volume;
    OWNER on a table or catalog), expansion refuses it.

    Expected: BundleError is raised for every combination.
    """
    with pytest.raises(BundleError):
        expand_bundle(bundle, obj, "a.b.c")


# ---- compile: matching / include / exclude / dedup ----------------------

def _cfg(grant_rules, labels=None, tags=None):
    return {
        "base_grants": {"schema_version": "1.0", "grant_rules": grant_rules},
        "labels": {"schema_version": "1.0", "labels": labels or []},
        "tags": {"schema_version": "1.0", "tags": tags or []},
        "policies": {"schema_version": "1.0"},
    }


def test_match_selects_labeled_objects_only():
    """A label-match rule selects only securables of the requested object type whose labels match.

    Expected: SELECT is granted on c.s.t1 only; c.s.t2 (other label value) and view c.s.v1 (type not
    requested) get nothing.
    """
    docs = _cfg(
        grant_rules=[{
            "groups": ["g"], "bundle": "READ",
            "match": {"object_types": ["table"], "labels": {"business_function": "investments"}},
        }],
        labels=[
            {"securable": {"type": "table", "name": "c.s.t1"}, "labels": {"business_function": "investments"}},
            {"securable": {"type": "table", "name": "c.s.t2"}, "labels": {"business_function": "compliance"}},
            {"securable": {"type": "view", "name": "c.s.v1"}, "labels": {"business_function": "investments"}},
        ],
    )
    st = compile_config(docs)
    selected = {g.securable_fqn for g in st.grants if g.privilege_type == "SELECT"}
    assert "c.s.t1" in selected          # matched table
    assert "c.s.t2" not in selected      # wrong label value
    assert "c.s.v1" not in selected      # wrong object_type (view not requested)


def test_labels_are_not_materialized_as_tags():
    """Labels drive grant selection but never become target tags.

    Expected: SELECT is granted on c.s.t1, and the only target tag is the acm_pii_type column tag on
    c.s.t1.name; no business_function tag appears.
    """
    docs = _cfg(
        grant_rules=[{
            "groups": ["g"], "bundle": "READ",
            "match": {"object_types": ["table"], "labels": {"business_function": "investments"}},
        }],
        labels=[{"securable": {"type": "table", "name": "c.s.t1"}, "labels": {"business_function": "investments"}}],
        tags=[{"securable": {"type": "column", "name": "c.s.t1.name"}, "tags": {"acm_pii_type": "FULL_NAME"}}],
    )
    st = compile_config(docs)
    assert {g.securable_fqn for g in st.grants if g.privilege_type == "SELECT"} == {"c.s.t1"}
    # only the column tag is materialized; the business_function label is not
    assert [(t.securable_type, t.securable_fqn, t.tag_key) for t in st.tags] == [
        ("column", "c.s.t1.name", "acm_pii_type")]
    assert all(t.tag_key != "business_function" for t in st.tags)


def test_data_product_label_selection():
    """A rule can select securables by their data_product label, including in a hyphenated catalog.

    Expected: SELECT is granted only on hera-dev.gold.esg_waci (data_product investment_esg), not on
    the market_data table.
    """
    docs = _cfg(
        grant_rules=[{
            "groups": ["hera-all-esg-read-unmasked"], "bundle": "READ",
            "match": {"object_types": ["table", "view"], "labels": {"data_product": "investment_esg"}},
        }],
        labels=[
            {"securable": {"type": "table", "name": "hera-dev.gold.esg_waci"},
             "labels": {"data_product": "investment_esg"}},
            {"securable": {"type": "table", "name": "hera-dev.silver.fact_price"},
             "labels": {"data_product": "market_data"}},
        ],
    )
    st = compile_config(docs)
    selected = {g.securable_fqn for g in st.grants if g.privilege_type == "SELECT"}
    assert selected == {"hera-dev.gold.esg_waci"}    # only the investment_esg-labeled table


def test_include_and_exclude():
    """A rule's include adds an extra securable and its exclude removes one the label match picked.

    Expected: grants on function c.s.f (included) and none on table c.s.t1, even though it matched.
    """
    docs = _cfg(
        grant_rules=[{
            "groups": ["g"], "bundle": "READ",
            "match": {"object_types": ["table"], "labels": {"business_function": "investments"}},
            "include": [{"type": "function", "name": "c.s.f"}],
            "exclude": [{"type": "table", "name": "c.s.t1"}],
        }],
        labels=[{"securable": {"type": "table", "name": "c.s.t1"}, "labels": {"business_function": "investments"}}],
    )
    st = compile_config(docs)
    fqns = {(g.securable_type, g.securable_fqn) for g in st.grants}
    assert ("function", "c.s.f") in fqns           # included
    assert ("table", "c.s.t1") not in fqns          # excluded despite matching


def test_dedup_across_groups_and_rules():
    """Grants from identical rules are de-duplicated.

    Expected: the grant list has no duplicates.
    """
    rule = {"groups": ["g"], "bundle": "READ",
            "match": {"object_types": ["table"], "labels": {"business_function": "investments"}}}
    docs = _cfg(
        grant_rules=[rule, dict(rule)],   # identical rule twice
        labels=[{"securable": {"type": "table", "name": "c.s.t"}, "labels": {"business_function": "investments"}}],
    )
    st = compile_config(docs)
    assert len(st.grants) == len(set(st.grants))    # no duplicates


def test_compile_error_surfaces_for_bad_bundle_object():
    """An unsupported bundle/object pair in a rule (WRITE on a function) becomes a compile error
    instead of crashing.

    Expected: state.ok is False and state.errors is non-empty.
    """
    docs = _cfg(
        grant_rules=[{"groups": ["g"], "bundle": "WRITE",
                      "include": [{"type": "function", "name": "c.s.f"}]}],
    )
    st = compile_config(docs)
    assert not st.ok and st.errors


# ---- column-mask policy target definition (for CREATE OR REPLACE DDL) ---

def test_column_mask_policy_carries_definition_and_exempt():
    """A column-mask policy carries its column-tag match and the mask-exempt principals for its
    EXCEPT clause.

    Expected: one policy matching acm_pii_type FULL_NAME, with no table tag match, and
    except_principals "hera-etl-a,hera-etl-b" (sorted, blank dropped).
    """
    docs = {"policies": {"schema_version": "1.0", "column_mask_policies": [
        {"scope": "hera-dev.gold", "column_matching_tags": {"acm_pii_type": "FULL_NAME"},
         "function": "hera-dev.governance.acm_mask_full_name"}]}}
    st = compile_config(docs, mask_exempt_principals=["hera-etl-b", "hera-etl-a", "  "])
    assert len(st.policies) == 1
    p = st.policies[0]
    assert p.column_tag_key == "acm_pii_type" and p.column_tag_value == "FULL_NAME"
    assert p.table_tag_key is None and p.table_tag_value is None      # no table_matching_tags
    assert p.except_principals == "hera-etl-a,hera-etl-b"             # sorted, blanks dropped


# ---- compile the ESG fixture config ---------------------------------------

def test_compile_config_esg():
    """The ESG fixture config (tests/fixtures/config/esg) compiles end to end.

    Expected: state.ok, with non-empty grants, tags, policies and refs, and every policy name
    starting with acm_policy_.
    """
    d = FIXTURES / "config" / "esg"
    docs = {k: yaml.safe_load((d / f"{k}.yaml").read_text())
            for k in ("base_grants", "rbac_view_grants", "rbac_views", "tags", "policies", "labels")
            if (d / f"{k}.yaml").exists()}
    st = compile_config(docs)
    assert st.ok
    assert st.grants and st.tags and st.policies and st.refs
    assert all(p.policy_name.startswith("acm_policy_") for p in st.policies)


# ---- skip_base_grants: base_grants.yaml rules are not compiled -------------

def test_include_base_grants_false_compiles_only_view_grants():
    """With include_base_grants False (skip_base_grants), base_grants.yaml rules are dropped and
    only the view grants compile.

    Expected: principals are {admins, viewers} normally but only {viewers} with base grants skipped;
    the result is ok and still has USE_CATALOG on catalog c from the view grant.
    """
    docs = _cfg([{"groups": ["admins"], "bundle": "READ",
                  "include": [{"type": "table", "name": "c.gold.t"}]}])
    docs["rbac_view_grants"] = {"schema_version": "1.0", "grant_rules": [
        {"groups": ["viewers"], "bundle": "READ", "include": [{"type": "schema", "name": "c.serving"}]}]}

    both = {g.principal for g in compile_config(docs).grants}
    views_only = compile_config(docs, include_base_grants=False)
    assert both == {"admins", "viewers"}
    assert {g.principal for g in views_only.grants} == {"viewers"}       # base rule dropped
    assert views_only.ok
    # the view grant still carries its own traversal (USE CATALOG) — it doesn't need base grants
    assert ("USE_CATALOG", "catalog", "c") in {(g.privilege_type, g.securable_type, g.securable_fqn)
                                              for g in views_only.grants}


# ---- bundle expansion: remaining branches -----------------------------------

def test_write_catalog_and_schema():
    """The WRITE bundle on a whole catalog or schema grants READ's privileges plus MODIFY at that
    level, which every table below inherits.

    Expected: on catalog a, exactly USE_CATALOG, USE_SCHEMA, SELECT and MODIFY (all on the catalog);
    on schema a.b, exactly USE_CATALOG on a plus USE_SCHEMA, SELECT and MODIFY on a.b.
    """
    assert S(expand_bundle("WRITE", "catalog", "a")) == {
        ("USE_CATALOG", "catalog", "a"), ("USE_SCHEMA", "catalog", "a"),
        ("SELECT", "catalog", "a"), ("MODIFY", "catalog", "a"),
    }
    assert S(expand_bundle("WRITE", "schema", "a.b")) == {
        ("USE_CATALOG", "catalog", "a"), ("USE_SCHEMA", "schema", "a.b"),
        ("SELECT", "schema", "a.b"), ("MODIFY", "schema", "a.b"),
    }


def test_read_unsupported_object_type_raises():
    """READ on an object type it has no meaning for (a column) is refused.

    Expected: BundleError "READ not supported for object type 'column'".
    """
    with pytest.raises(BundleError, match="READ not supported for object type 'column'"):
        expand_bundle("READ", "column", "a.b.c.d")


def test_unknown_bundle_raises():
    """A bundle name outside READ/WRITE/OWNER/ADMIN is refused.

    Expected: BundleError "unknown bundle 'SUPERUSER'".
    """
    with pytest.raises(BundleError, match="unknown bundle 'SUPERUSER'"):
        expand_bundle("SUPERUSER", "table", "a.b.c")
