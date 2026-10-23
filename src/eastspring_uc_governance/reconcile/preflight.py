"""Preflight checks (pure Python, no Spark).

``check_existence``: for every object/function the config references, confirm it resolves in live
state. Missing ones become warnings (they do not block the deltas).
``check_rls_table``: the RLS allow-list table exists with the columns the views semi-join on (the
notebook stops an apply on these — every RLS view would fail).
"""
from __future__ import annotations

from typing import Any

from ..compile.model import RefObject

Warning = dict[str, Any]

# object_type -> the information_schema view the live reader should have populated
EXISTENCE_SOURCE = {
    "catalog": "information_schema.catalogs",
    "schema": "information_schema.schemata",
    "table": "information_schema.tables",
    "view": "information_schema.views",
    "function": "information_schema.routines",
    "volume": "information_schema.volumes",
    "column": "information_schema.columns",
}


def check_existence(refs: list[RefObject], existing: set[tuple[str, str]]) -> list[Warning]:
    """``existing`` is the set of (object_type, fqn) present live. Returns a warning per
    referenced object/function that is absent. Tables and views are treated as
    interchangeable (a view may surface under either information_schema view)."""
    warnings: list[Warning] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        ident = (ref.object_type, ref.fqn)
        if ident in seen:
            continue
        seen.add(ident)
        if _present(ref.object_type, ref.fqn, existing):
            continue
        warnings.append({
            "kind": "missing_object",
            "object_type": ref.object_type,
            "fqn": ref.fqn,
            "origin": ref.origin,
            "reason": f"not found in {EXISTENCE_SOURCE.get(ref.object_type, 'information_schema')}",
        })
    return sorted(warnings, key=lambda w: (w["object_type"], w["fqn"]))


def _present(object_type: str, fqn: str, existing: set[tuple[str, str]]) -> bool:
    if (object_type, fqn) in existing:
        return True
    # table/view leniency
    if object_type == "view" and ("table", fqn) in existing:
        return True
    if object_type == "table" and ("view", fqn) in existing:
        return True
    return False


def check_rls_table(table: str | None, columns: set[str] | None, *, group_column: str,
                    rls_columns: set[str]) -> list[Warning]:
    """The RLS allow-list the dynamic views semi-join (``rls_entitlement_table``) must exist and
    carry the group column plus every ``rls_column`` the views use. ``columns`` are the table's
    live column names (None = not found). Returns one warning per problem; [] when it fits or no
    view needs RLS. Column names compare case-insensitively (as in Unity Catalog)."""
    if not rls_columns:
        return []
    if not table:
        return [{"kind": "rls_table_missing", "object_type": "table", "fqn": "",
                 "origin": "reference.rls_entitlement_table",
                 "reason": f"views use rls_column {sorted(rls_columns)} but the reference has no "
                           f"rls_entitlement_table"}]
    if columns is None:
        return [{"kind": "rls_table_missing", "object_type": "table", "fqn": table,
                 "origin": "reference.rls_entitlement_table",
                 "reason": "not found — create it first (apply_functions, for the "
                           "group_portfolio_access MV)"}]
    have = {c.lower() for c in columns}
    return [{"kind": "rls_column_missing", "object_type": "column", "fqn": f"{table}.{col}",
             "origin": "reference.rls_group_column" if col == group_column else "rbac_views.rls_column",
             "reason": f"{table} has no column {col!r} (it has: {', '.join(sorted(have))})"}
            for col in [group_column, *sorted(rls_columns - {group_column})] if col.lower() not in have]
