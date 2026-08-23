"""Read live Unity Catalog state via Spark (runs in Databricks).

Uses ``system.information_schema`` (cross-catalog, confirmed available on the test
workspace) for grants/tags/objects, and ``SHOW POLICIES`` for ABAC policies (which
are not exposed in information_schema). ``spark`` is the session the Job runs with.

Grant/tag/policy readers return **Spark DataFrames** (never collected) so the Spark-SQL
diff scales to a full catalog's grants. Columns match the target frames in ``frames.py``:
grants ``(principal, privilege_type, securable_type, securable_fqn)``; tags
``(securable_type, securable_fqn, tag_key, tag_value)``; policies
``(scope, policy_name, policy_type, function)``. ``read_existing_objects`` still returns a
small driver-side set (referenced-object existence for preflight).

NOTE (verify during e2e): exact column names of routine_privileges (``specific_*``),
the ``inherited_from`` sentinel value, and the ``SHOW POLICIES`` output columns are
confirmed against the live workspace and adjusted if needed.
"""
from __future__ import annotations

from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType, StructField, StringType

Row = dict[str, Any]
_SYS = "system.information_schema"

_POLICY_COLS = ["scope", "policy_name", "policy_type", "function"]


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


_SCOPE_KEYWORD = {"catalog": "CATALOG", "schema": "SCHEMA", "table": "TABLE"}


def read_live_policies(spark: SparkSession, scopes: list[tuple[str, str]]) -> DataFrame:
    """Run SHOW POLICIES for each (scope_type, fqn), normalize to
    (scope, policy_name, policy_type, function), and return a DataFrame. ``scopes`` is
    de-duplicated. Policies are few, so this collects per scope then rebuilds a frame for
    a uniform diff input.

    SHOW POLICIES column names vary; we map defensively. Verify/adjust during e2e."""
    rows: list[tuple] = []
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
            rows.append((fqn, name, ptype, func))
    if not rows:
        return _empty(spark, _POLICY_COLS)
    schema = StructType([StructField(c, StringType(), True) for c in _POLICY_COLS])
    return spark.createDataFrame(rows, schema)


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
