"""Execute the generated DDL against Unity Catalog via Spark (runs in Databricks).

``apply`` takes planned statements (``statement.plan_phase`` / ``plan_all``) and either previews
them (dry-run) or runs each via ``spark.sql``, capturing a per-statement result. Within a list,
execution never stops on a failure — one bad statement (e.g. a missing principal) is recorded and
the rest continue (continue-and-report).

``apply_phases`` runs whole phases in order (masking -> views -> access -> loosen, see
``statement.PHASES``) with one exception to continue-and-report: if a statement in a *blocking*
phase (masking) FAILS, every later statement is SKIPPED — views and grants must never go out
while a column's mask is missing.
"""
from __future__ import annotations

from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType, StructField, StringType

Row = dict[str, Any]

RESULT_COLS = ["phase", "dimension", "change_type", "securable", "principal", "sql", "status", "error"]


def _securable(row: Row) -> str:
    return row.get("securable_fqn") or row.get("scope") or ""


def apply(spark: SparkSession, planned: list[tuple[str, Row, str]], *,
          dry_run: bool = True) -> list[Row]:
    """Run (or preview) planned statements. ``planned`` is a list of
    ``(dimension, row, sql)``. Returns one result dict per statement:
    status is PLANNED (dry-run), SUCCEEDED, or FAILED (with ``error``)."""
    results: list[Row] = []
    for dimension, row, sql in planned:
        result = {
            "dimension": dimension,
            "change_type": row.get("change_type"),
            "securable": _securable(row),
            "principal": row.get("principal"),
            "sql": sql,
            "status": "PLANNED",
            "error": None,
        }
        if not dry_run:
            try:
                spark.sql(sql)
                result["status"] = "SUCCEEDED"
            except Exception as exc:  # continue-and-report: one failure never blocks the rest
                result["status"] = "FAILED"
                result["error"] = str(exc)
        results.append(result)
    return results


def apply_phases(spark: SparkSession, phases: list[tuple[str, list[tuple[str, Row, str]]]], *,
                 dry_run: bool = True, blocking: tuple[str, ...] = ("masking",)) -> list[Row]:
    """Run (or preview) ``[(phase, planned)]`` in order; each result also carries its ``phase``.
    If any statement of a ``blocking`` phase FAILED, the statements of every later phase are not
    run: status SKIPPED, with the reason in ``error``."""
    results: list[Row] = []
    blocked_by: str | None = None
    for phase, planned in phases:
        if blocked_by:
            phase_results = apply(spark, planned, dry_run=True)     # builds the rows, runs nothing
            for r in phase_results:
                r["status"] = "SKIPPED"
                r["error"] = f"not run: the {blocked_by} phase had a failure"
        else:
            phase_results = apply(spark, planned, dry_run=dry_run)
            if phase in blocking and any(r["status"] == "FAILED" for r in phase_results):
                blocked_by = phase
        for r in phase_results:
            r["phase"] = phase
        results.extend(phase_results)
    return results


def results_frame(spark: SparkSession, results: list[Row]) -> DataFrame:
    """The per-statement results as a Spark DataFrame (all string columns)."""
    schema = StructType([StructField(c, StringType(), True) for c in RESULT_COLS])
    data = [tuple(None if r.get(c) is None else str(r.get(c)) for c in RESULT_COLS)
            for r in results]
    return spark.createDataFrame(data, schema)
