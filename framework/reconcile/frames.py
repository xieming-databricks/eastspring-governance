"""Build Spark DataFrames for target state, deltas, and warnings (runs in Databricks).

All columns are strings so empty result sets still get an explicit schema (Spark can't
infer one from an empty list). Unknown extra keys on rows (e.g. a live policy's ``raw``)
are dropped by projecting only the known columns.
"""
from __future__ import annotations

from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType, StructField, StringType

from ..compile.model import TargetGrant, TargetPolicy, TargetState, TargetTag

Row = dict[str, Any]

GRANT_COLS = ["principal", "privilege_type", "securable_type", "securable_fqn"]
TAG_COLS = ["securable_type", "securable_fqn", "tag_key", "tag_value"]
POLICY_COLS = ["scope", "policy_name", "policy_type", "function"]
WARN_COLS = ["kind", "object_type", "fqn", "origin", "reason"]


# ---- target dataclass -> row dict ---------------------------------------

def grant_row(g: TargetGrant) -> Row:
    return {"principal": g.principal, "privilege_type": g.privilege_type,
            "securable_type": g.securable_type, "securable_fqn": g.securable_fqn}


def tag_row(t: TargetTag) -> Row:
    return {"securable_type": t.securable_type, "securable_fqn": t.securable_fqn,
            "tag_key": t.tag_key, "tag_value": t.tag_value}


def policy_row(p: TargetPolicy) -> Row:
    return {"scope": p.scope, "policy_name": p.policy_name,
            "policy_type": p.policy_type, "function": p.function}


def _df(spark: SparkSession, rows: list[dict], columns: list[str]) -> DataFrame:
    schema = StructType([StructField(c, StringType(), True) for c in columns])
    data = [tuple(None if r.get(c) is None else str(r.get(c)) for c in columns) for r in rows]
    return spark.createDataFrame(data, schema)


def target_frames(spark: SparkSession, state: TargetState) -> dict[str, DataFrame]:
    """Compiled target state as Spark DataFrames — the diff's target inputs (and the
    'translate YAML to several DataFrames' deliverable)."""
    return {
        "grants": _df(spark, [grant_row(g) for g in state.grants], GRANT_COLS),
        "tags": _df(spark, [tag_row(t) for t in state.tags], TAG_COLS),
        "policies": _df(spark, [policy_row(p) for p in state.policies], POLICY_COLS),
    }


def warnings_frame(spark: SparkSession, warnings: list[dict]) -> DataFrame:
    return _df(spark, warnings, WARN_COLS)
