"""Read live Unity Catalog state via Spark (runs in Databricks).

Uses ``system.information_schema`` (cross-catalog, confirmed available on the test
workspace) for grants/tags/objects, and — for ABAC column-mask policies — the
``abac_policy_definitions`` information relation plus per-policy ``DESCRIBE POLICY`` (the
relation gives the inventory + EXCEPT list but not the mask function). ``spark`` is the
session the Job runs with.

Grant/tag/policy readers return **Spark DataFrames** (never collected) so the Spark-SQL
diff scales to a full catalog's grants. Columns match the target frames in ``frames.py``:
grants ``(principal, privilege_type, securable_type, securable_fqn)``; tags
``(securable_type, securable_fqn, tag_key, tag_value)``; policies
``(scope, policy_name, policy_type, function, except_principals)``. ``read_existing_objects``
still returns a small driver-side set (referenced-object existence for preflight).

NOTE (verify during e2e): exact column names of routine_privileges (``specific_*``), the
``inherited_from`` sentinel value, and the ``abac_policy_definitions`` / ``DESCRIBE POLICY``
columns (``abac_policy_definitions`` is Public Preview) are confirmed against the live
workspace and adjusted if needed.
"""
from __future__ import annotations

from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType, StructField, StringType

Row = dict[str, Any]
_SYS = "system.information_schema"

_POLICY_COLS = ["scope", "policy_name", "policy_type", "function", "except_principals"]


def _in_list(vals) -> str:
    uniq = sorted({v for v in vals if v})
    return ",".join("'" + v.replace("'", "''") + "'" for v in uniq)


def _empty(spark: SparkSession, cols: list[str]) -> DataFrame:
    schema = StructType([StructField(c, StringType(), True) for c in cols])
    return spark.createDataFrame([], schema)


def read_live_grants(spark: SparkSession, catalogs: list[str]) -> DataFrame:
    """Direct grants (inherited_from = 'NONE') on the given catalogs, unioned across the
    five privilege views into (principal, privilege_type, securable_type, securable_fqn).
    Returns a DataFrame (not collected) so the diff runs in Spark."""
    cats = _in_list(catalogs)
    if not cats:
        return _empty(spark, ["principal", "privilege_type", "securable_type", "securable_fqn"])
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
    return spark.sql(sql)


def read_live_tags(spark: SparkSession, catalogs: list[str]) -> DataFrame:
    """All tag assignments on the given catalogs, unioned across the five tag views into
    (securable_type, securable_fqn, tag_key, tag_value). Returns a DataFrame; the diff
    filters to acm_* keys."""
    cats = _in_list(catalogs)
    if not cats:
        return _empty(spark, ["securable_type", "securable_fqn", "tag_key", "tag_value"])
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
    return spark.sql(sql)


def read_existing_objects(spark: SparkSession, catalogs: list[str]) -> set[tuple[str, str]]:
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


def abac_policies_unavailable(spark: SparkSession) -> str | None:
    """None if ``abac_policy_definitions`` (Public Preview) can be read, else why not. Without it
    the live policies are unknown: the diff would plan every target policy as ADD and could never
    see one to DROP — so the notebook warns in plan mode and refuses to apply."""
    try:
        spark.sql(f"SELECT policy_name FROM {_SYS}.abac_policy_definitions LIMIT 1").collect()
        return None
    except Exception as exc:
        first = (str(exc).strip().splitlines() or [""])[0][:200]
        return f"{_SYS}.abac_policy_definitions is not readable ({type(exc).__name__}: {first})"


def empty_policies(spark: SparkSession) -> DataFrame:
    """An empty live-policies frame (the plan-mode stand-in when the ABAC relation is unavailable)."""
    return _empty(spark, _POLICY_COLS)


def read_live_policies(spark: SparkSession, catalogs: list[str]) -> DataFrame:
    """Live column-mask policies in ``catalogs``, read from the ABAC information relation
    (inventory + EXCEPT list) plus a per-policy ``DESCRIBE POLICY`` for the mask function (the
    relation doesn't expose function_name). Normalized to
    (scope, policy_name, policy_type, function, except_principals) for the diff. Only
    ``acm_policy_*`` COLUMN_MASK policies (the framework-managed set) are read.

    ``abac_policy_definitions`` is Public Preview. If it can't be read this RAISES — there is no
    silent "no live policies" fallback; check ``abac_policies_unavailable`` first and decide (the
    notebook: warn + ``empty_policies`` in plan, stop in apply). A failing ``DESCRIBE POLICY`` only
    leaves that policy's function unknown (planned as a CHANGE: an idempotent CREATE OR REPLACE)."""
    cats = _in_list(catalogs)
    if not cats:
        return _empty(spark, _POLICY_COLS)
    scope_sql = ("CASE on_securable_type "
                 "WHEN 'CATALOG' THEN catalog_name "
                 "WHEN 'SCHEMA' THEN concat_ws('.', catalog_name, schema_name) "
                 "ELSE concat_ws('.', catalog_name, schema_name, securable_name) END")
    inv = spark.sql(f"""
        SELECT policy_name,
               on_securable_type,
               {scope_sql} AS scope,
               coalesce(array_join(array_sort(except_principals), ','), '') AS except_principals
          FROM {_SYS}.abac_policy_definitions
         WHERE policy_type = 'COLUMN_MASK'
           AND policy_name LIKE 'acm_policy_%'
           AND catalog_name IN ({cats})
    """).collect()

    rows: list[tuple] = []
    for r in inv:
        fn = _describe_policy_function(spark, r["policy_name"], r["on_securable_type"], r["scope"])
        rows.append((r["scope"], r["policy_name"], "column_mask", fn, r["except_principals"]))
    if not rows:
        return _empty(spark, _POLICY_COLS)
    schema = StructType([StructField(c, StringType(), True) for c in _POLICY_COLS])
    return spark.createDataFrame(rows, schema)


def _bt(fqn: str) -> str:
    """Backtick each dotted segment (scope fqns can contain hyphens, e.g. hera-dev)."""
    return ".".join(f"`{seg}`" for seg in fqn.split("."))


def _describe_policy_function(spark: SparkSession, name: str, scope_kw: str, scope: str) -> str | None:
    """Mask function fqn from DESCRIBE POLICY (info_name='Function Name'); None if unavailable."""
    try:
        df = spark.sql(f"DESCRIBE POLICY `{name}` ON {scope_kw} {_bt(scope)}")
    except Exception:
        return None
    cols = {c.lower(): c for c in df.columns}
    for d in (row.asDict() for row in df.collect()):
        key = _pick(d, cols, ["info_name", "col_name", "name"])
        if key and str(key).strip().lower() in ("function name", "function_name", "function"):
            return _pick(d, cols, ["info_value", "data_type", "value"])
    return None


def _pick(d: dict, cols: dict, candidates: list[str]):
    for cand in candidates:
        if cand in cols:
            return d.get(cols[cand])
    return None
