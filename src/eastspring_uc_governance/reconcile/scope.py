"""Scope helpers: derive the catalogs to read, the policy scopes to SHOW, and the
protected set that full-sync must never propose for removal.

Pure Python (no Spark), so the notebook and any caller share one implementation.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..compile.model import TargetState

# System schema auto-created in every Unity Catalog catalog (read-only system views,
# system-owned). Its grants/tags are never framework-managed, so full-sync skips it.
SYSTEM_SCHEMAS: frozenset[str] = frozenset({"information_schema"})


def _catalog_of(fqn: Any, cats: set[str]) -> None:
    if isinstance(fqn, str) and fqn:
        cats.add(fqn.split(".")[0])


def _clean(values: Iterable[str] | None) -> set[str]:
    """Non-empty, stripped strings from an optional iterable."""
    return {v.strip() for v in (values or []) if isinstance(v, str) and v.strip()}


def catalogs_from_config(docs: dict[str, Any]) -> list[str]:
    """Distinct catalogs = first segment of every securable FQN referenced in the raw
    config: tag securables, grant include/exclude objects, and policy scopes + functions.
    Scans the config directly (no compile needed)."""
    cats: set[str] = set()

    grants = docs.get("grants") or {}
    for rule in grants.get("grant_rules") or []:
        for key in ("include", "exclude"):
            for obj in rule.get(key) or []:
                _catalog_of(obj.get("name"), cats)

    tags = docs.get("tags") or {}
    for entry in tags.get("tags") or []:
        _catalog_of((entry.get("securable") or {}).get("name"), cats)

    policies = docs.get("policies") or {}
    for pol in policies.get("column_mask_policies") or []:
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


# ---- protected set (full-sync guardrail) --------------------------------
#
# Full-sync makes the target catalog framework-owned: any grant in it the YAML does
# not declare becomes a REMOVE. These two helpers assemble the exceptions that must
# never be removed. Ownership is deliberately NOT protected here: it lives outside
# the grant layer, so revoking an owner's redundant grant removes nothing they still
# hold via ownership. See docs/full-sync-scope.md.

def protected_principals(*, deployer: str | None = None,
                         deployers: Iterable[str] | None = None,
                         admin_groups: Iterable[str] | None = None) -> set[str]:
    """Principals full-sync must never REMOVE:

    ``deployer``      the run-as identity (``current_user()``) — resolved at run time,
                      self-lockout insurance for whoever actually runs the reconcile.
    ``deployers``     declared deployment identities/groups from the reference allow-list
                      (``protected_principals.deployers``) — protects the production service
                      principal/group even when a human runs the notebook interactively
                      (``current_user()`` would miss it).
    ``admin_groups``  account/metastore admin groups from the reference allow-list
                      (``protected_principals.admin_groups``).
    """
    result: set[str] = set()
    if deployer:
        result |= _clean([deployer])
    result |= _clean(deployers)
    result |= _clean(admin_groups)
    return result


def protected_schemas(*, extra: Iterable[str] | None = None) -> set[str]:
    """Schema names full-sync must never touch: :data:`SYSTEM_SCHEMAS` plus any
    ``protected_schemas`` from the reference allow-list."""
    return set(SYSTEM_SCHEMAS) | _clean(extra)
