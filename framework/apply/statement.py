"""Pure DDL generation: reconcile delta row -> a GRANT / REVOKE / ALTER … TAGS statement.

No Spark here, so this is fully unit-testable offline. The Spark ``executor`` runs the
strings this module produces.

Inputs are the delta-row dicts produced by ``reconcile.diff`` (with a ``change_type``):
  * grants   — {principal, privilege_type, securable_type, securable_fqn, change_type}
  * tags     — {securable_type, securable_fqn, tag_key, tag_value, change_type}
  * policies — {scope, policy_name, function, except_principals, change_type}; ADD/CHANGE also
               need the compiled target definition (``policy_defs[(scope, policy_name)]``) for the
               MATCH COLUMNS / mask function, since the delta row alone doesn't carry them.

No dialect conversion is needed: UC accepts the underscore ``privilege_type`` form
(``USE_CATALOG``) directly in GRANT/REVOKE — verified against the workspace — and SQL
keywords are case-insensitive, so ``securable_type.upper()`` is passed as the ``ON`` /
``ALTER`` keyword as-is. Rows therefore flow straight from information_schema into DDL.

Column-mask policies are applied as ABAC DDL: ADD/CHANGE -> ``CREATE OR REPLACE POLICY``,
REMOVE -> ``DROP POLICY``.
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


# ---- ABAC column-mask policies ------------------------------------------

def _scope_keyword(scope: str) -> str:
    """CATALOG / SCHEMA / TABLE from a scope fqn's dotted depth (mirrors compile._scope_type)."""
    return {0: "CATALOG", 1: "SCHEMA", 2: "TABLE"}.get(scope.count("."), "TABLE")


def policy_statement(row: Row, policy_def: dict | None) -> str:
    """ADD/CHANGE -> CREATE OR REPLACE POLICY (from the compiled ``policy_def``);
    REMOVE -> DROP POLICY (from the delta ``row`` — the live-only row has no target def)."""
    scope, name, ct = row["scope"], row["policy_name"], row["change_type"]
    kw = _scope_keyword(scope)
    if ct == "REMOVE":
        return f"DROP POLICY {name} ON {kw} {_qident(scope)}"
    if ct in ("ADD", "CHANGE"):
        if not policy_def:
            raise ApplyError(f"no target definition for policy {scope}.{name}")
        return _create_or_replace_policy(policy_def)
    raise ApplyError(f"unexpected policy change_type {ct!r}")


def _create_or_replace_policy(d: dict) -> str:
    """Build the ABAC column-mask DDL from a compiled policy definition dict."""
    lines = [f"CREATE OR REPLACE POLICY {d['policy_name']} ON {_scope_keyword(d['scope'])} {_qident(d['scope'])}",
             f"COLUMN MASK {_qident(d['function'])}",
             "TO `account users`"]
    exempt = [p for p in (d.get("except_principals") or "").split(",") if p]
    if exempt:
        lines.append("EXCEPT " + ", ".join(_qprincipal(p) for p in exempt))
    lines.append("FOR TABLES")
    if d.get("table_tag_key"):
        lines.append(f"WHEN has_tag_value({_qlit(d['table_tag_key'])}, {_qlit(d['table_tag_value'])})")
    lines.append(f"MATCH COLUMNS has_tag_value({_qlit(d['column_tag_key'])}, {_qlit(d['column_tag_value'])}) AS c")
    lines.append("ON COLUMN c")
    return "\n".join(lines)


# ---- dynamic views (view-based RLS) -------------------------------------

def view_statement(spec: dict, *, entitlement_table: str, group_col: str = "ad_group") -> str:
    """CREATE OR REPLACE VIEW for one ``rbac_views.yaml`` entry (dict: view / source / rls_column?).

    ``rls_column`` set → a query-level semi-join to the entitlement allow-list (prunes via DFP;
    NOT a row-filter UDF): rows where the fact's rls_column is in the caller's entitled set,
    ``is_account_group_member`` evaluated on the allow-list's group column. Absent → pass-through.
    """
    view, source = spec["view"], spec["source"]
    rls_col = spec.get("rls_column")
    if not rls_col:
        return f"CREATE OR REPLACE VIEW {_qident(view)} AS SELECT * FROM {_qident(source)}"
    col = _qident(rls_col)
    return (f"CREATE OR REPLACE VIEW {_qident(view)} AS\n"
            f"SELECT f.* FROM {_qident(source)} f\n"
            f"WHERE f.{col} IN (\n"
            f"  SELECT {col} FROM {_qident(entitlement_table)}\n"
            f"  WHERE is_account_group_member({_qident(group_col)}))")


def drop_view_statement(fqn: str) -> str:
    return f"DROP VIEW IF EXISTS {_qident(fqn)}"


# ---- dispatch -----------------------------------------------------------

# ADD before REMOVE within a dimension: grant/set new state before revoking the old,
# to minimize any window of lost access. CHANGE sits with ADD.
_ORDER = {"ADD": 0, "CHANGE": 1, "REMOVE": 2}


def statements_for(dimension: str, rows: list[Row], *,
                   policy_defs: dict | None = None) -> list[tuple[Row, str]]:
    """(row, sql) pairs for a dimension's delta rows, ADD/CHANGE before REMOVE.

    ``policy_defs`` maps ``(scope, policy_name) -> compiled definition dict`` and is required to
    build CREATE OR REPLACE for policy ADD/CHANGE (ignored for grants/tags)."""
    if dimension == "grants":
        builder = grant_statement
    elif dimension == "tags":
        builder = tag_statement
    elif dimension == "policies":
        defs = policy_defs or {}

        def builder(row):   # policies need the compiled def; grants/tags don't
            return policy_statement(row, defs.get((row["scope"], row["policy_name"])))
    else:
        raise ApplyError(f"unknown dimension {dimension!r}")

    ordered = sorted(rows, key=lambda r: _ORDER.get(r.get("change_type", ""), 9))
    return [(row, builder(row)) for row in ordered]


# Dimensions the apply stage executes, in order (grants/tags first, then column-mask policies).
APPLIED_DIMENSIONS = ("grants", "tags", "policies")


def plan_all(deltas: dict[str, list[Row]], *,
             policy_defs: dict | None = None) -> list[tuple[str, Row, str]]:
    """Flatten a whole delta into ordered ``(dimension, row, sql)`` triples ready for the
    executor. ``deltas`` maps dimension -> delta rows (the notebook's ``delta_counts``).
    ``policy_defs`` (``{(scope, policy_name): definition}``) is needed to build policy CREATE DDL."""
    planned: list[tuple[str, Row, str]] = []
    for dim in APPLIED_DIMENSIONS:
        for row, stmt in statements_for(dim, deltas.get(dim) or [], policy_defs=policy_defs):
            planned.append((dim, row, stmt))
    return planned
