"""Scope helpers: derive the catalogs to read and the policy scopes to SHOW.

Pure Python (no Spark), so the notebook and any caller share one implementation.
"""
from __future__ import annotations

from typing import Any

from ..compile.model import TargetState


def _catalog_of(fqn: Any, cats: set[str]) -> None:
    if isinstance(fqn, str) and fqn:
        cats.add(fqn.split(".")[0])


def catalogs_from_config(docs: dict[str, Any]) -> list[str]:
    """Distinct catalogs = first segment of every securable FQN referenced in the raw
    config: tag securables, grant include/exclude objects, and policy scopes + functions.
    Scans the config directly (no compile needed)."""
    cats: set[str] = set()

    grants = docs.get("grants") or docs.get("grants.yaml") or {}
    for rule in grants.get("grant_rules") or []:
        for key in ("include", "exclude"):
            for obj in rule.get(key) or []:
                _catalog_of(obj.get("name"), cats)

    tags = docs.get("tags") or docs.get("tags.yaml") or {}
    for entry in tags.get("tags") or []:
        _catalog_of((entry.get("securable") or {}).get("name"), cats)

    policies = docs.get("policies") or docs.get("policies.yaml") or {}
    for key in ("column_mask_policies", "row_filter_policies"):
        for pol in policies.get(key) or []:
            _catalog_of(pol.get("scope"), cats)
            _catalog_of(pol.get("function"), cats)

    return sorted(cats)


def infer_catalogs(state: TargetState) -> list[str]:
    """Distinct catalogs referenced anywhere in the compiled target state."""
    cats: set[str] = set()
    for g in state.grants:
        cats.add(g.securable_fqn.split(".")[0])
    for t in state.tags:
        cats.add(t.securable_fqn.split(".")[0])
    for p in state.policies:
        cats.add(p.scope.split(".")[0])
    for r in state.refs:
        cats.add(r.fqn.split(".")[0])
    return sorted(c for c in cats if c)


def policy_scopes(state: TargetState) -> list[tuple[str, str]]:
    """(scope_type, fqn) for each target policy, for SHOW POLICIES ON <type> <fqn>."""
    def scope_type(scope: str) -> str:
        return {0: "catalog", 1: "schema", 2: "table"}.get(scope.count("."), "table")
    return [(scope_type(p.scope), p.scope) for p in state.policies]
