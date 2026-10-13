"""Render the packaged SQL objects (mask UDFs, the group_portfolio_access MV) for one deployment.

The ``sql/*.sql`` files ship inside the package and contain no deployment specifics: the catalog
and governance schema are ``${catalog}`` / ``${governance_schema}`` placeholders, filled in from
the env's ``config/reference/<env>.yaml``:

- ``catalog``           <- ``target_catalog``
- ``governance_schema`` <- the one ``governance_schemas`` entry under ``target_catalog``
  (bare name, e.g. ``governance`` for ``hera-dev.governance``)

Rendering fails loudly on a missing value or an unknown placeholder (``string.Template.substitute``),
so a typo never reaches ``spark.sql``. Pure Python — no Spark, so it is unit-testable.
"""
from __future__ import annotations

from importlib import resources
from string import Template
from typing import Any


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
            "governance_schema": governance[0].split(".", 1)[1].replace("`", "``")}


def render_sql_objects(reference: dict[str, Any]) -> list[tuple[str, str]]:
    """``[(file_name, sql)]`` for every packaged ``sql/*.sql``, in name order, placeholders filled
    and any trailing ``;`` removed (``spark.sql`` takes a single statement)."""
    values = sql_variables(reference)
    sql_dir = resources.files(__package__).joinpath("sql")
    rendered: list[tuple[str, str]] = []
    for f in sorted((f for f in sql_dir.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name):
        raw = f.read_text().strip()
        stmt = raw[:-1].strip() if raw.endswith(";") else raw
        rendered.append((f.name, Template(stmt).substitute(values)))
    return rendered
