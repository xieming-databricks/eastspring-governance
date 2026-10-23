"""Render a deployment's SQL objects (mask UDFs, the group_portfolio_access MV) for one env.

The ``*.sql`` templates are NOT in the package: they are deployment content and live with the
config (e.g. ``sql/esg/`` in the configs repo), passed in as ``sql_dir`` — never guessed. They
contain no env specifics: the catalog and governance schema are ``${catalog}`` /
``${governance_schema}`` placeholders, filled in from the env's reference YAML:

- ``catalog``            <- ``target_catalog``
- ``governance_schema``  <- the one ``governance_schemas`` entry under ``target_catalog``
  (bare name, e.g. ``governance`` for ``hera-dev.governance``)
- ``unmasked_condition`` <- the ``groups`` whose name ends in ``unmasked``, as
  ``is_account_group_member('<name>') OR …`` (``FALSE`` when there are none: nobody is unmasked)

Rendering fails loudly on a missing value or an unknown placeholder (``string.Template.substitute``),
so a typo never reaches ``spark.sql``. Pure Python — no Spark, so it is unit-testable.
"""
from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any, Iterable


UNMASKED_SUFFIX = "unmasked"   # groups named *unmasked read raw values (inlined in every acm_mask_*)


def unmasked_groups(reference: dict[str, Any]) -> list[str]:
    """Names of the reference ``groups`` that may read unmasked values: those ending in
    ``unmasked``, in reference order, de-duplicated."""
    names = [g.get("name") if isinstance(g, dict) else g for g in reference.get("groups") or []]
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n.endswith(UNMASKED_SUFFIX)))


def _unmasked_condition(groups: list[str]) -> str:
    # a SQL boolean expression for a mask's `WHEN`; group names are single-quoted literals (quotes
    # doubled). Continuation lines are indented to sit under `WHEN` in the mask UDFs.
    if not groups:
        return "FALSE"                 # fail closed: no unmasked group -> everyone sees masked
    return "\n           OR ".join("is_account_group_member('" + g.replace("'", "''") + "')" for g in groups)


def sql_variables(reference: dict[str, Any]) -> dict[str, str]:
    """The placeholder values for ``reference``. Raises ValueError if they can't be derived."""
    catalog = (reference.get("target_catalog") or "").strip()
    if not catalog:
        raise ValueError("reference has no target_catalog")
    governance = [s for s in reference.get("governance_schemas") or [] if s.startswith(f"{catalog}.")]
    if len(governance) != 1:
        raise ValueError(f"expected exactly one governance_schemas entry under {catalog!r}, "
                         f"found {governance}")
    # values go inside backtick-quoted identifiers in the SQL; double any backtick to stay quoted.
    return {"catalog": catalog.replace("`", "``"),
            "governance_schema": governance[0].split(".", 1)[1].replace("`", "``"),
            "unmasked_condition": _unmasked_condition(unmasked_groups(reference))}


def load_sql_templates(sql_dir: str | Path) -> list[tuple[str, str]]:
    """``[(file_name, raw_sql)]`` for every ``*.sql`` in ``sql_dir``, in name order. Raises
    ValueError if the path is empty, not a directory, or holds no ``.sql`` file."""
    if not str(sql_dir).strip():
        raise ValueError("SQL folder path is empty")
    path = Path(sql_dir)
    if not path.is_dir():
        raise ValueError(f"SQL folder not found: {path}")
    files = sorted((f for f in path.iterdir() if f.name.endswith(".sql") and f.is_file()),
                   key=lambda f: f.name)
    if not files:
        raise ValueError(f"no .sql files in {path}")
    return [(f.name, f.read_text(encoding="utf-8")) for f in files]


def render_sql(raw: str, values: dict[str, str]) -> str:
    """One template -> one statement: placeholders filled (an unknown one raises KeyError, a
    malformed ``$`` raises ValueError) and any trailing ``;`` removed (``spark.sql`` takes a single
    statement)."""
    stmt = raw.strip()
    stmt = stmt[:-1].strip() if stmt.endswith(";") else stmt
    return Template(stmt).substitute(values)


def render_sql_objects(reference: dict[str, Any], sql_dir: str | Path) -> list[tuple[str, str]]:
    """``[(file_name, sql)]`` for every ``*.sql`` in ``sql_dir``, in name order, rendered for
    ``reference``. Raises on a bad folder, a missing value or an unknown placeholder."""
    values = sql_variables(reference)
    return [(name, render_sql(raw, values)) for name, raw in load_sql_templates(sql_dir)]


def unknown_allow_list_groups(allow_list_groups: Iterable[str], reference: dict[str, Any]) -> list[str]:
    """Groups in the RLS allow-list (``rls_entitlement_table``'s group column) that are not in the
    reference's ``groups``, sorted. Such rows grant nothing: ``is_account_group_member`` never
    matches a name nobody is a member of — typically a renamed group whose entitlement rows still
    carry the old name, so its members silently see no rows."""
    known = {g.get("name") if isinstance(g, dict) else g for g in reference.get("groups") or []}
    return sorted({g for g in allow_list_groups if g and g not in known})
