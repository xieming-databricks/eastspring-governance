"""Pure DDL generation: reconcile delta row -> a GRANT / REVOKE / ALTER … TAGS statement.

No Spark here, so this is fully unit-testable offline. The Spark ``executor`` runs the
strings this module produces.

Inputs are the delta-row dicts produced by ``reconcile.diff`` (with a ``change_type``):
  * grants — {principal, privilege_type, securable_type, securable_fqn, change_type}
  * tags   — {securable_type, securable_fqn, tag_key, tag_value, change_type}

No dialect conversion is needed: UC accepts the underscore ``privilege_type`` form
(``USE_CATALOG``) directly in GRANT/REVOKE — verified against the workspace — and SQL
keywords are case-insensitive, so ``securable_type.upper()`` is passed as the ``ON`` /
``ALTER`` keyword as-is. Rows therefore flow straight from information_schema into DDL.

Policies are detect-only in v1: ``statements_for("policies", …)`` returns ``[]``.
"""
from __future__ import annotations

from typing import Any

Row = dict[str, Any]


class ApplyError(ValueError):
    """Raised for a delta row that cannot be turned into a statement."""


# ---- quoting / formatting helpers ---------------------------------------

def _qident(fqn: str) -> str:
    """Backtick-quote each dotted segment of an identifier: a.b.c -> `a`.`b`.`c`."""
    return ".".join(f"`{seg.replace('`', '``')}`" for seg in fqn.split("."))


def _qprincipal(principal: str) -> str:
    """Backtick-quote a principal (groups have hyphens, users are emails)."""
    return f"`{principal.replace('`', '``')}`"


def _qlit(value: str) -> str:
    """Single-quote a string literal (tag key / value), escaping embedded quotes."""
    return "'" + str(value).replace("'", "''") + "'"


# ---- per-dimension statement builders -----------------------------------

def grant_statement(row: Row) -> str:
    """ADD -> GRANT, REMOVE -> REVOKE. Grants have no CHANGE."""
    priv = row["privilege_type"]                       # underscore form accepted by UC as-is
    target = f"{row['securable_type'].upper()} {_qident(row['securable_fqn'])}"
    principal = _qprincipal(row["principal"])
    ct = row["change_type"]
    if ct == "ADD":
        return f"GRANT {priv} ON {target} TO {principal}"
    if ct == "REMOVE":
        return f"REVOKE {priv} ON {target} FROM {principal}"
    raise ApplyError(f"unexpected grant change_type {ct!r}")


def tag_statement(row: Row) -> str:
    """ADD/CHANGE -> SET TAGS, REMOVE -> UNSET TAGS. Column securables use ALTER COLUMN."""
    st = row["securable_type"]
    fqn = row["securable_fqn"]
    key = row["tag_key"]
    ct = row["change_type"]

    if st == "column":
        parts = fqn.split(".")
        if len(parts) != 4:
            raise ApplyError(f"column fqn must be 4-part, got {fqn!r}")
        target = f"TABLE {_qident('.'.join(parts[:3]))} ALTER COLUMN {_qident(parts[3])}"
    else:
        target = f"{st.upper()} {_qident(fqn)}"

    if ct in ("ADD", "CHANGE"):
        return f"ALTER {target} SET TAGS ({_qlit(key)} = {_qlit(row['tag_value'])})"
    if ct == "REMOVE":
        return f"ALTER {target} UNSET TAGS ({_qlit(key)})"
    raise ApplyError(f"unexpected tag change_type {ct!r}")


# ---- dispatch -----------------------------------------------------------

# ADD before REMOVE within a dimension: grant/set new state before revoking the old,
# to minimize any window of lost access. CHANGE sits with ADD.
_ORDER = {"ADD": 0, "CHANGE": 1, "REMOVE": 2}


def statements_for(dimension: str, rows: list[Row]) -> list[tuple[Row, str]]:
    """(row, sql) pairs for a dimension's delta rows. 'policies' -> [] (detect-only in v1)."""
    if dimension == "grants":
        builder = grant_statement
    elif dimension == "tags":
        builder = tag_statement
    elif dimension == "policies":
        return []
    else:
        raise ApplyError(f"unknown dimension {dimension!r}")

    ordered = sorted(rows, key=lambda r: _ORDER.get(r.get("change_type", ""), 9))
    return [(row, builder(row)) for row in ordered]


# Dimensions the apply stage executes, in order. Policies are detect-only (see above).
APPLIED_DIMENSIONS = ("grants", "tags")


def plan_all(deltas: dict[str, list[Row]]) -> list[tuple[str, Row, str]]:
    """Flatten a whole delta into ordered ``(dimension, row, sql)`` triples ready for the
    executor. ``deltas`` maps dimension -> delta rows (the notebook's ``delta_counts``).
    Only ``APPLIED_DIMENSIONS`` produce statements; policies are skipped."""
    planned: list[tuple[str, Row, str]] = []
    for dim in APPLIED_DIMENSIONS:
        for row, stmt in statements_for(dim, deltas.get(dim) or []):
            planned.append((dim, row, stmt))
    return planned
