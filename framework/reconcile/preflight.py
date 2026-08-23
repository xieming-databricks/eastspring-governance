"""Preflight existence check (pure Python, no Spark).

For every object/function the config references, confirm it resolves in live state.
Missing ones become warnings (they do not block the deltas).
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
