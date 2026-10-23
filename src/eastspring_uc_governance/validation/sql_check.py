"""Check a deployment's SQL folder (e.g. ``sql/esg``) against its reference + config.

The mask UDFs and the RLS allow-list MV are deployment content, not package code, so they get the
checks their unit tests used to give them, plus the cross-file ones nothing checked before:

- every file renders for the reference (no unknown placeholder)                       -> error
- every ``acm_mask_<x>.sql`` defines ``${catalog}.${governance_schema}.acm_mask_<x>``  -> error
- every ``acm_mask_*.sql`` uses ``${unmasked_condition}`` (who reads raw values comes from the
  reference's ``*unmasked`` groups, never from a name typed into one mask), on a code line   -> error
- every Masking Rules function (policies.yaml) has its mask file                        -> error
- views with an ``rls_column`` need the reference's ``rls_entitlement_table``           -> error
- some file creates that ``rls_entitlement_table``                                      -> warning
  (a warning, not an error: it may be a customer-maintained table; the reconcile checks it exists
  at run time)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..sql_objects import load_sql_templates, render_sql, sql_variables
from .validator import Check, Issue

_LAYER = "sql"
_MASK_PREFIX = "acm_mask_"
# CREATE [OR REPLACE] FUNCTION `${catalog}`.`${governance_schema}`.<name>(
_MASK_DEF = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
                       r"`\$\{catalog\}`\.`\$\{governance_schema\}`\.`?(\w+)`?\s*\(", re.IGNORECASE)
# CREATE [OR REPLACE] [MATERIALIZED] VIEW | TABLE <fqn> — in the RENDERED sql
_CREATES = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+VIEW|VIEW|TABLE)\s+"
                      r"(?:IF\s+NOT\s+EXISTS\s+)?((?:`[^`]+`|[\w-]+)(?:\.(?:`[^`]+`|[\w-]+)){2})",
                      re.IGNORECASE)


def _plain(fqn: str) -> str:
    """`a`.`b`.c -> a.b.c (compare names without quoting)."""
    return ".".join(seg.strip("`") for seg in re.findall(r"`[^`]+`|[^.]+", fqn))


def _placeholder_in_comment(raw: str) -> bool:
    # the multi-line ${unmasked_condition}, substituted inside a `--` comment, would push its
    # 2nd+ lines out of the comment and break (or change) the SQL
    return any("--" in line.split("${unmasked_condition}")[0]
               for line in raw.splitlines() if "${unmasked_condition}" in line)


def check_sql_dir(sql_dir: str | Path, reference: dict[str, Any],
                  docs: dict[str, Any]) -> tuple[list[Issue], list[Check]]:
    """Issues + checks for ``sql_dir``. ``docs`` are the parsed config files keyed by file name
    (``policies.yaml``, ``rbac_views.yaml``, …), as in ``validate_dir``."""
    sql_dir = Path(sql_dir)
    issues: list[Issue] = []
    try:
        templates = load_sql_templates(sql_dir)
    except ValueError as exc:
        issues.append(Issue(sql_dir.name or str(sql_dir), "", str(exc), _LAYER))
        return issues, [Check("sql", str(exc), "missing")]
    try:
        values = sql_variables(reference)
    except ValueError as exc:
        issues.append(Issue("reference", "", f"cannot render the SQL: {exc}", _LAYER))
        return issues, [Check("sql", f"{len(templates)} file(s); not rendered: {exc}", "issues")]
    catalog, governance = (values[k].replace("``", "`") for k in ("catalog", "governance_schema"))

    masks: dict[str, str] = {}         # mask function fqn (plain) -> file name
    created: dict[str, str] = {}       # object fqn the SQL creates (plain) -> file name
    for name, raw in templates:
        try:
            rendered = render_sql(raw, values)
        except (KeyError, ValueError) as exc:
            issues.append(Issue(name, "", f"does not render: unknown or malformed placeholder {exc}",
                                _LAYER))
            continue
        for m in _CREATES.finditer(rendered):
            created[_plain(m.group(1))] = name
        if not name.startswith(_MASK_PREFIX):
            continue
        stem = name[:-len(".sql")]
        defined = _MASK_DEF.search(raw)
        if not defined or defined.group(1) != stem:
            issues.append(Issue(name, "", f"must define the function `${{catalog}}`.`${{governance_schema}}`"
                                          f".{stem} (found: {defined.group(1) if defined else 'none'})",
                                _LAYER))
        else:
            masks[f"{catalog}.{governance}.{stem}"] = name
        if "${unmasked_condition}" not in raw:
            issues.append(Issue(name, "", "does not use ${unmasked_condition}: who reads raw values must "
                                          "come from the reference's *unmasked groups", _LAYER))
        elif _placeholder_in_comment(raw):
            issues.append(Issue(name, "", "${unmasked_condition} appears after `--` on a line: its "
                                          "later lines would escape the comment", _LAYER))

    # Masking Rules (policies.yaml) -> each function needs its mask file
    rules = (docs.get("policies.yaml") or {}).get("column_mask_policies") or []
    for i, rule in enumerate(rules):
        fn = str(rule.get("function") or "")
        if fn and _plain(fn) not in masks:
            issues.append(Issue("policies.yaml", f"column_mask_policies/{i}/function",
                                f"mask function {fn!r} has no file in {sql_dir.name}/ (expected "
                                f"{_plain(fn).rsplit('.', 1)[-1]}.sql defining it)", _LAYER))

    # RLS views -> the allow-list table they semi-join
    views = (docs.get("rbac_views.yaml") or {}).get("views") or []
    rls_views = [v for v in views if isinstance(v, dict) and v.get("rls_column")]
    rls_table = str(reference.get("rls_entitlement_table") or "").strip()
    if rls_views and not rls_table:
        issues.append(Issue("rbac_views.yaml", "views",
                            f"{len(rls_views)} view(s) set rls_column but the reference has no "
                            f"rls_entitlement_table", "reference"))
    elif rls_views and _plain(rls_table) not in created:
        same_name = sorted(f for f in created if f.rsplit(".", 1)[-1] == _plain(rls_table).rsplit(".", 1)[-1])
        hint = f"; {sql_dir.name}/ creates {', '.join(same_name)} — wrong schema?" if same_name else ""
        issues.append(Issue("reference", "rls_entitlement_table",
                            f"{rls_table} is not created by any file in {sql_dir.name}/ — it must "
                            f"already exist (the reconcile checks it at run time){hint}",
                            _LAYER, "warning"))

    detail = (f"{len(templates)} file(s), {len(masks)} mask function(s); {len(rules)} masking rule(s) "
              f"-> mask files; RLS table {rls_table or '-'} for {len(rls_views)} RLS view(s)")
    status = "ok" if not issues else ("warnings" if all(i.severity == "warning" for i in issues) else "issues")
    return issues, [Check("sql", detail, status)]
