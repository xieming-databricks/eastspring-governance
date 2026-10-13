"""Tests for the pure-Python compile step (bundles + matching + expansion)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.compile import compile_config
from eastspring_uc_governance.compile.bundles import BundleError, expand_bundle

REPO = Path(__file__).resolve().parents[1]


def S(privs):
    return set(privs)


# ---- bundle expansion ---------------------------------------------------

def test_read_table_traversal_and_select():
    assert S(expand_bundle("READ", "table", "a.b.c")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "schema", "a.b"),
        ("SELECT", "table", "a.b.c"),
    }


def test_read_view_grants_like_table():
    assert S(expand_bundle("READ", "view", "a.b.v")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "schema", "a.b"),
        ("SELECT", "table", "a.b.v"),
    }


def test_read_catalog_inherits_use_schema():
    assert S(expand_bundle("READ", "catalog", "a")) == {
        ("USE_CATALOG", "catalog", "a"),
        ("USE_SCHEMA", "catalog", "a"),
        ("SELECT", "catalog", "a"),
    }


def test_read_function_and_volume():
    assert ("EXECUTE", "function", "a.b.f") in expand_bundle("READ", "function", "a.b.f")
    assert ("READ_VOLUME", "volume", "a.b.vol") in expand_bundle("READ", "volume", "a.b.vol")


def test_write_table_adds_modify():
    got = S(expand_bundle("WRITE", "table", "a.b.c"))
    assert ("SELECT", "table", "a.b.c") in got
    assert ("MODIFY", "table", "a.b.c") in got


def test_owner_schema_create_privileges():
    got = S(expand_bundle("OWNER", "schema", "a.b"))
    assert {("CREATE_TABLE", "schema", "a.b"),
            ("CREATE_FUNCTION", "schema", "a.b"),
            ("CREATE_VOLUME", "schema", "a.b")} <= got


def test_admin_catalog_manage():
    assert ("MANAGE", "catalog", "a") in expand_bundle("ADMIN", "catalog", "a")


@pytest.mark.parametrize("bundle,obj", [("WRITE", "function"), ("WRITE", "volume"),
                                        ("OWNER", "table"), ("OWNER", "catalog")])
def test_unsupported_combo_raises(bundle, obj):
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
    """Labels drive grant selection but must NEVER appear in the target tag state."""
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
    """data_product is a valid label key (free-string value); grant selects by it, hyphenated catalog ok."""
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
    rule = {"groups": ["g"], "bundle": "READ",
            "match": {"object_types": ["table"], "labels": {"business_function": "investments"}}}
    docs = _cfg(
        grant_rules=[rule, dict(rule)],   # identical rule twice
        labels=[{"securable": {"type": "table", "name": "c.s.t"}, "labels": {"business_function": "investments"}}],
    )
    st = compile_config(docs)
    assert len(st.grants) == len(set(st.grants))    # no duplicates


def test_compile_error_surfaces_for_bad_bundle_object():
    docs = _cfg(
        grant_rules=[{"groups": ["g"], "bundle": "WRITE",
                      "include": [{"type": "function", "name": "c.s.f"}]}],
    )
    st = compile_config(docs)
    assert not st.ok and st.errors


# ---- column-mask policy target definition (for CREATE OR REPLACE DDL) ---

def test_column_mask_policy_carries_definition_and_exempt():
    docs = {"policies": {"schema_version": "1.0", "column_mask_policies": [
        {"scope": "hera-dev.gold", "column_matching_tags": {"acm_pii_type": "FULL_NAME"},
         "function": "hera-dev.governance.acm_mask_full_name"}]}}
    st = compile_config(docs, mask_exempt_principals=["hera-etl-b", "hera-etl-a", "  "])
    assert len(st.policies) == 1
    p = st.policies[0]
    assert p.column_tag_key == "acm_pii_type" and p.column_tag_value == "FULL_NAME"
    assert p.table_tag_key is None and p.table_tag_value is None      # no table_matching_tags
    assert p.except_principals == "hera-etl-a,hera-etl-b"             # sorted, blanks dropped


# ---- compile the real config/esg ----------------------------------------

def test_compile_config_esg():
    d = REPO / "config" / "esg"
    docs = {k: yaml.safe_load((d / f"{k}.yaml").read_text())
            for k in ("base_grants", "rbac_view_grants", "rbac_views", "tags", "policies", "labels")
            if (d / f"{k}.yaml").exists()}
    st = compile_config(docs)
    assert st.ok
    assert st.grants and st.tags and st.policies and st.refs
    assert all(p.policy_name.startswith("acm_policy_") for p in st.policies)
