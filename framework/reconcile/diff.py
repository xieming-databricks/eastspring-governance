"""Per-dimension diff of target vs. live state (pure Python, no Spark).

Each dimension produces delta rows tagged ``change_type`` = ADD / REMOVE / CHANGE.
Rows are plain dicts so they convert trivially to Spark DataFrames downstream.

Scope safety: REMOVE is only ever proposed within the framework-managed scope —
grants only for principals the config grants to; tags only for ``acm_*`` keys;
policies only for ``acm_policy_*`` names. Unrelated live state is never touched.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..compile.model import TargetGrant, TargetPolicy, TargetState, TargetTag

Row = dict[str, Any]

# ---- target dataclass -> row dict ---------------------------------------

def grant_row(g: TargetGrant) -> Row:
    return {"principal": g.principal, "privilege_type": g.privilege_type,
            "securable_type": g.securable_type, "securable_fqn": g.securable_fqn}


def tag_row(t: TargetTag) -> Row:
    return {"securable_type": t.securable_type, "securable_fqn": t.securable_fqn,
            "tag_key": t.tag_key, "tag_value": t.tag_value}


def policy_row(p: TargetPolicy) -> Row:
    return {"scope": p.scope, "policy_name": p.policy_name,
            "policy_type": p.policy_type, "function": p.function}


# ---- generic diff -------------------------------------------------------

def _diff(target: Iterable[Row], live: Iterable[Row],
          key_cols: list[str], value_cols: list[str]) -> list[Row]:
    def key(r: Row) -> tuple:
        return tuple(r[c] for c in key_cols)

    tmap = {key(r): r for r in target}
    lmap = {key(r): r for r in live}
    out: list[Row] = []
    for k, tr in tmap.items():
        lr = lmap.get(k)
        if lr is None:
            out.append({**tr, "change_type": "ADD"})
        elif value_cols and any(tr.get(c) != lr.get(c) for c in value_cols):
            out.append({**tr, "change_type": "CHANGE"})
    for k, lr in lmap.items():
        if k not in tmap:
            out.append({**lr, "change_type": "REMOVE"})
    return out


# ---- per-dimension diffs (with managed-scope filtering) -----------------

def diff_grants(target: list[Row], live: list[Row]) -> list[Row]:
    """Grants have no mutable value -> ADD / REMOVE only. REMOVE is limited to
    principals the config grants to (managed principals)."""
    managed = {r["principal"] for r in target}
    live_scoped = [r for r in live if r["principal"] in managed]
    keys = ["principal", "privilege_type", "securable_type", "securable_fqn"]
    return _diff(target, live_scoped, keys, value_cols=[])


def diff_tags(target: list[Row], live: list[Row]) -> list[Row]:
    """REMOVE/CHANGE limited to acm_* tag keys."""
    live_scoped = [r for r in live if str(r["tag_key"]).startswith("acm_")]
    keys = ["securable_type", "securable_fqn", "tag_key"]
    return _diff(target, live_scoped, keys, value_cols=["tag_value"])


def diff_policies(target: list[Row], live: list[Row]) -> list[Row]:
    """REMOVE/CHANGE limited to acm_policy_* policy names."""
    live_scoped = [r for r in live if str(r["policy_name"]).startswith("acm_policy_")]
    keys = ["scope", "policy_name"]
    return _diff(target, live_scoped, keys, value_cols=["policy_type", "function"])


# ---- convenience: diff a whole TargetState against live rows ------------

def diff_all(state: TargetState, live_grants: list[Row], live_tags: list[Row],
             live_policies: list[Row]) -> dict[str, list[Row]]:
    return {
        "grants": diff_grants([grant_row(g) for g in state.grants], live_grants),
        "tags": diff_tags([tag_row(t) for t in state.tags], live_tags),
        "policies": diff_policies([policy_row(p) for p in state.policies], live_policies),
    }
