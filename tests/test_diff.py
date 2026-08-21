"""Tests for the per-dimension diff (change_type + managed-scope filtering)."""
from __future__ import annotations

from framework.reconcile import diff


def _ct(rows):
    """change_type -> set of identities, for compact assertions."""
    out = {}
    for r in rows:
        out.setdefault(r["change_type"], []).append(r)
    return out


# ---- grants: ADD / REMOVE, unmanaged principals ignored -----------------

def test_grants_add_remove_and_scope():
    target = [
        {"principal": "grp", "privilege_type": "SELECT", "securable_type": "table", "securable_fqn": "c.s.t"},
    ]
    live = [
        # managed principal, not in target -> REMOVE
        {"principal": "grp", "privilege_type": "SELECT", "securable_type": "table", "securable_fqn": "c.s.old"},
        # unmanaged principal -> ignored entirely
        {"principal": "other", "privilege_type": "SELECT", "securable_type": "table", "securable_fqn": "c.s.x"},
    ]
    by = _ct(diff.diff_grants(target, live))
    assert [r["securable_fqn"] for r in by.get("ADD", [])] == ["c.s.t"]
    assert [r["securable_fqn"] for r in by.get("REMOVE", [])] == ["c.s.old"]
    # the unmanaged principal never appears
    assert all(r["principal"] != "other" for rows in by.values() for r in rows)


def test_grants_no_change_type():
    # grants have no mutable value; a present grant yields nothing
    row = {"principal": "grp", "privilege_type": "SELECT", "securable_type": "table", "securable_fqn": "c.s.t"}
    assert diff.diff_grants([row], [dict(row)]) == []


# ---- tags: ADD / REMOVE / CHANGE, non-acm ignored -----------------------

def test_tags_add_remove_change():
    target = [
        {"securable_type": "table", "securable_fqn": "c.s.t", "tag_key": "acm_business_function", "tag_value": "investments"},
        {"securable_type": "column", "securable_fqn": "c.s.t.col", "tag_key": "acm_pii_type", "tag_value": "FULL_NAME"},
    ]
    live = [
        # same key, different value -> CHANGE
        {"securable_type": "table", "securable_fqn": "c.s.t", "tag_key": "acm_business_function", "tag_value": "compliance"},
        # extra acm_ key not in target -> REMOVE
        {"securable_type": "table", "securable_fqn": "c.s.t", "tag_key": "acm_rls", "tag_value": "region"},
        # non-acm live tag -> ignored
        {"securable_type": "table", "securable_fqn": "c.s.t", "tag_key": "cost_center", "tag_value": "42"},
    ]
    by = _ct(diff.diff_tags(target, live))
    assert {r["securable_fqn"] for r in by.get("ADD", [])} == {"c.s.t.col"}
    assert {r["tag_key"] for r in by.get("CHANGE", [])} == {"acm_business_function"}
    assert {r["tag_key"] for r in by.get("REMOVE", [])} == {"acm_rls"}
    assert all(r["tag_key"] != "cost_center" for rows in by.values() for r in rows)


# ---- policies: CHANGE on function, non-acm_policy ignored ---------------

def test_policies_change_and_scope():
    target = [
        {"scope": "c.s", "policy_name": "acm_policy_mask_x__h1", "policy_type": "column_mask", "function": "c.g.new_fn"},
    ]
    live = [
        # same (scope,name), different function -> CHANGE
        {"scope": "c.s", "policy_name": "acm_policy_mask_x__h1", "policy_type": "column_mask", "function": "c.g.old_fn"},
        # non-framework policy -> ignored
        {"scope": "c.s", "policy_name": "someones_policy", "policy_type": "column_mask", "function": "c.g.z"},
    ]
    by = _ct(diff.diff_policies(target, live))
    assert {r["policy_name"] for r in by.get("CHANGE", [])} == {"acm_policy_mask_x__h1"}
    assert "ADD" not in by and "REMOVE" not in by
    assert all(r["policy_name"] != "someones_policy" for rows in by.values() for r in rows)
