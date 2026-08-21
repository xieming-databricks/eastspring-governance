"""Read live Unity Catalog state via Spark (runs in Databricks).

Uses ``system.information_schema`` (cross-catalog, confirmed available on the test
workspace) for grants/tags/objects, and ``SHOW POLICIES`` for ABAC policies (which
are not exposed in information_schema). ``spark`` is the session the Job runs with.

Returns plain row dicts matching the shapes in ``diff.py`` so the pure-Python diff
can consume them directly.

NOTE (verify during e2e): exact column names of routine_privileges (``specific_*``),
the ``inherited_from`` sentinel value, and the ``SHOW POLICIES`` output columns are
confirmed against the live workspace and adjusted if needed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

Row = dict[str, Any]
_SYS = "system.information_schema"


def _in_list(vals) -> str:
    uniq = sorted({v for v in vals if v})
    return ",".join("'" + v.replace("'", "''") + "'" for v in uniq)


def read_live_grants(spark: "SparkSession", catalogs: list[str]) -> list[Row]:
    """Direct grants (inherited_from = 'NONE') on the config's catalogs, unioned across
    the five privilege views into (principal, privilege_type, securable_type, fqn)."""
    cats = _in_list(catalogs)
    if not cats:
        return []
    sql = f"""
    SELECT grantee AS principal, privilege_type, 'catalog' AS securable_type,
           catalog_name AS securable_fqn
      FROM {_SYS}.catalog_privileges
     WHERE inherited_from = 'NONE' AND catalog_name IN ({cats})
    UNION ALL
    SELECT grantee, privilege_type, 'schema',
           concat_ws('.', catalog_name, schema_name)
      FROM {_SYS}.schema_privileges
     WHERE inherited_from = 'NONE' AND catalog_name IN ({cats})
    UNION ALL
    SELECT grantee, privilege_type, 'table',
           concat_ws('.', table_catalog, table_schema, table_name)
      FROM {_SYS}.table_privileges
     WHERE inherited_from = 'NONE' AND table_catalog IN ({cats})
    UNION ALL
    SELECT grantee, privilege_type, 'function',
           concat_ws('.', specific_catalog, specific_schema, specific_name)
      FROM {_SYS}.routine_privileges
     WHERE inherited_from = 'NONE' AND specific_catalog IN ({cats})
    UNION ALL
    SELECT grantee, privilege_type, 'volume',
           concat_ws('.', volume_catalog, volume_schema, volume_name)
      FROM {_SYS}.volume_privileges
     WHERE inherited_from = 'NONE' AND volume_catalog IN ({cats})
    """
    return [r.asDict() for r in spark.sql(sql).collect()]


def read_live_tags(spark: "SparkSession", catalogs: list[str]) -> list[Row]:
    """acm_* and any other tag assignments on the config's catalogs, unioned across the
    five tag views into (securable_type, fqn, tag_key, tag_value)."""
    cats = _in_list(catalogs)
    if not cats:
        return []
    sql = f"""
    SELECT 'catalog' AS securable_type, catalog_name AS securable_fqn,
           tag_name AS tag_key, tag_value
      FROM {_SYS}.catalog_tags WHERE catalog_name IN ({cats})
    UNION ALL
    SELECT 'schema', concat_ws('.', catalog_name, schema_name), tag_name, tag_value
      FROM {_SYS}.schema_tags WHERE catalog_name IN ({cats})
    UNION ALL
    SELECT 'table', concat_ws('.', catalog_name, schema_name, table_name), tag_name, tag_value
      FROM {_SYS}.table_tags WHERE catalog_name IN ({cats})
    UNION ALL
    SELECT 'column', concat_ws('.', catalog_name, schema_name, table_name, column_name),
           tag_name, tag_value
      FROM {_SYS}.column_tags WHERE catalog_name IN ({cats})
    UNION ALL
    SELECT 'volume', concat_ws('.', catalog_name, schema_name, volume_name), tag_name, tag_value
      FROM {_SYS}.volume_tags WHERE catalog_name IN ({cats})
    """
    return [r.asDict() for r in spark.sql(sql).collect()]


def read_existing_objects(spark: "SparkSession", catalogs: list[str]) -> set[tuple[str, str]]:
    """Set of (object_type, fqn) that exist live, for the preflight check."""
    cats = _in_list(catalogs)
    existing: set[tuple[str, str]] = set()
    if not cats:
        return existing

    for r in spark.sql(f"SELECT catalog_name FROM {_SYS}.catalogs WHERE catalog_name IN ({cats})").collect():
        existing.add(("catalog", r["catalog_name"]))
    for r in spark.sql(f"SELECT catalog_name, schema_name FROM {_SYS}.schemata WHERE catalog_name IN ({cats})").collect():
        existing.add(("schema", f"{r['catalog_name']}.{r['schema_name']}"))
    for r in spark.sql(
        f"SELECT table_catalog, table_schema, table_name, table_type FROM {_SYS}.tables "
        f"WHERE table_catalog IN ({cats})"
    ).collect():
        fqn = f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}"
        existing.add(("view" if r["table_type"] == "VIEW" else "table", fqn))
    for r in spark.sql(
        f"SELECT table_catalog, table_schema, table_name, column_name FROM {_SYS}.columns "
        f"WHERE table_catalog IN ({cats})"
    ).collect():
        existing.add(("column",
                      f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}.{r['column_name']}"))
    for r in spark.sql(
        f"SELECT routine_catalog, routine_schema, routine_name FROM {_SYS}.routines "
        f"WHERE routine_catalog IN ({cats})"
    ).collect():
        existing.add(("function",
                      f"{r['routine_catalog']}.{r['routine_schema']}.{r['routine_name']}"))
    for r in spark.sql(
        f"SELECT volume_catalog, volume_schema, volume_name FROM {_SYS}.volumes "
        f"WHERE volume_catalog IN ({cats})"
    ).collect():
        existing.add(("volume",
                      f"{r['volume_catalog']}.{r['volume_schema']}.{r['volume_name']}"))
    return existing


_SCOPE_KEYWORD = {"catalog": "CATALOG", "schema": "SCHEMA", "table": "TABLE"}


def read_live_policies(spark: "SparkSession", scopes: list[tuple[str, str]]) -> list[Row]:
    """Run SHOW POLICIES for each (scope_type, fqn) and normalize to
    (scope, policy_name, policy_type, function). ``scopes`` is de-duplicated.

    SHOW POLICIES column names vary; we map defensively and record the raw row under
    'raw' for debugging. Verify/adjust the column mapping during e2e."""
    out: list[Row] = []
    for scope_type, fqn in sorted(set(scopes)):
        kw = _SCOPE_KEYWORD.get(scope_type)
        if kw is None:
            continue
        try:
            df = spark.sql(f"SHOW POLICIES ON {kw} {fqn}")
        except Exception:
            continue  # scope may not exist yet; preflight surfaces that separately
        cols = {c.lower(): c for c in df.columns}
        for r in df.collect():
            d = r.asDict()
            name = _pick(d, cols, ["policy_name", "name", "policyname"])
            ptype = _norm_policy_type(_pick(d, cols, ["policy_type", "kind", "type"]))
            func = _pick(d, cols, ["function", "function_name", "mask_function", "filter_function"])
            if name is None:
                continue
            out.append({"scope": fqn, "policy_name": name, "policy_type": ptype,
                        "function": func, "raw": d})
    return out


def _pick(d: dict, cols: dict, candidates: list[str]):
    for cand in candidates:
        if cand in cols:
            return d.get(cols[cand])
    return None


def _norm_policy_type(v) -> str:
    s = str(v or "").upper()
    if "MASK" in s:
        return "column_mask"
    if "FILTER" in s or "ROW" in s:
        return "row_filter"
    return s.lower()
