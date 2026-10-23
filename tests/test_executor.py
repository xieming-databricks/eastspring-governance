"""Tests for the apply executor's phase ordering + masking gate (a fake spark, no cluster)."""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")   # executor imports pyspark types at module level

from eastspring_uc_governance.apply import executor  # noqa: E402


class _FakeSpark:
    """Records every spark.sql call; raises for statements containing ``fail_on``."""

    def __init__(self, fail_on: str | None = None):
        self.ran, self.fail_on = [], fail_on

    def sql(self, stmt):
        self.ran.append(stmt)
        if self.fail_on and self.fail_on in stmt:
            raise RuntimeError(f"boom: {stmt}")


def _p(dim, sql, change_type="ADD"):
    return (dim, {"change_type": change_type, "securable_fqn": f"c.s.{sql}"}, sql)


def _phases():
    return [("masking", [_p("policies", "POLICY_A"), _p("tags", "TAG_A")]),
            ("views", [_p("views", "VIEW_A", "ENSURE")]),
            ("access", [_p("grants", "GRANT_A")]),
            ("loosen", [_p("tags", "UNSET_A", "REMOVE")])]


def test_phases_run_in_order_and_carry_their_phase():
    """Apply runs the phases in the order given and tags each result with its phase.

    Expected: spark ran POLICY, TAG, VIEW, GRANT, UNSET in that order; all SUCCEEDED.
    """
    spark = _FakeSpark()
    res = executor.apply_phases(spark, _phases(), dry_run=False)
    assert spark.ran == ["POLICY_A", "TAG_A", "VIEW_A", "GRANT_A", "UNSET_A"]
    assert [r["phase"] for r in res] == ["masking", "masking", "views", "access", "loosen"]
    assert {r["status"] for r in res} == {"SUCCEEDED"}


def test_masking_failure_skips_views_grants_and_loosening():
    """If a masking statement fails, the rest of masking still runs (continue-and-report), but no
    view, grant or loosening statement runs at all.

    Expected: spark ran only POLICY_A and TAG_A; POLICY_A FAILED; the later three SKIPPED with a
    reason naming the masking phase.
    """
    spark = _FakeSpark(fail_on="POLICY_A")
    res = executor.apply_phases(spark, _phases(), dry_run=False)
    assert spark.ran == ["POLICY_A", "TAG_A"]
    assert [r["status"] for r in res] == ["FAILED", "SUCCEEDED", "SKIPPED", "SKIPPED", "SKIPPED"]
    assert all("masking phase" in r["error"] for r in res if r["status"] == "SKIPPED")


def test_failure_outside_masking_does_not_block():
    """A failure in a non-blocking phase (here a grant) is reported and the run continues.

    Expected: everything ran; only GRANT_A FAILED; nothing SKIPPED.
    """
    spark = _FakeSpark(fail_on="GRANT_A")
    res = executor.apply_phases(spark, _phases(), dry_run=False)
    assert len(spark.ran) == 5
    assert [r["status"] for r in res] == ["SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "FAILED", "SUCCEEDED"]


def test_plan_mode_runs_nothing_and_lists_every_statement_in_order():
    """In plan mode no statement runs; the results list every statement in apply order.

    Expected: spark ran nothing; 5 PLANNED results in phase order.
    """
    spark = _FakeSpark()
    res = executor.apply_phases(spark, _phases(), dry_run=True)
    assert spark.ran == []
    assert [r["sql"] for r in res] == ["POLICY_A", "TAG_A", "VIEW_A", "GRANT_A", "UNSET_A"]
    assert {r["status"] for r in res} == {"PLANNED"}
