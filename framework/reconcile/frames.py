"""Build Spark DataFrames for target state, deltas, and warnings (runs in Databricks).

All columns are strings so empty result sets still get an explicit schema (Spark can't
infer one from an empty list). Unknown extra keys on rows (e.g. a live policy's ``raw``)
are dropped by projecting only the known columns.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import diff
from ..compile.model import TargetState

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession

GRANT_COLS = ["principal", "privilege_type", "securable_type", "securable_fqn"]
TAG_COLS = ["securable_type", "securable_fqn", "tag_key", "tag_value"]
POLICY_COLS = ["scope", "policy_name", "policy_type", "function"]
WARN_COLS = ["kind", "object_type", "fqn", "origin", "reason"]

_DELTA_COLS = {
    "grants": GRANT_COLS + ["change_type"],
    "tags": TAG_COLS + ["change_type"],
    "policies": POLICY_COLS + ["change_type"],
}


def _df(spark: "SparkSession", rows: list[dict], columns: list[str]) -> "DataFrame":
    from pyspark.sql.types import StructType, StructField, StringType
    schema = StructType([StructField(c, StringType(), True) for c in columns])
    data = [tuple(None if r.get(c) is None else str(r.get(c)) for c in columns) for r in rows]
    return spark.createDataFrame(data, schema)


def target_frames(spark: "SparkSession", state: TargetState) -> dict[str, "DataFrame"]:
    """The 'translate YAML to several Spark DataFrames' deliverable — target state."""
    return {
        "grants": _df(spark, [diff.grant_row(g) for g in state.grants], GRANT_COLS),
        "tags": _df(spark, [diff.tag_row(t) for t in state.tags], TAG_COLS),
        "policies": _df(spark, [diff.policy_row(p) for p in state.policies], POLICY_COLS),
    }


def delta_frames(spark: "SparkSession", deltas: dict[str, list[dict]]) -> dict[str, "DataFrame"]:
    """One delta DataFrame per dimension, each with a change_type column."""
    return {dim: _df(spark, deltas.get(dim, []), _DELTA_COLS[dim]) for dim in _DELTA_COLS}


def warnings_frame(spark: "SparkSession", warnings: list[dict]) -> "DataFrame":
    return _df(spark, warnings, WARN_COLS)
