"""Tests for the live readers' no-silent-fallback rule (a fake spark, no cluster)."""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")   # live imports pyspark types at module level

from eastspring_uc_governance.reconcile import live  # noqa: E402


class _Rows:
    def collect(self):
        return []


class _FakeSpark:
    def __init__(self, fail: Exception | None = None):
        self.fail, self.ran = fail, []

    def sql(self, stmt):
        self.ran.append(stmt)
        if self.fail:
            raise self.fail
        return _Rows()


def test_abac_relation_available():
    """When abac_policy_definitions can be read, there is no reason to stop.

    Expected: None, after one query against system.information_schema.abac_policy_definitions.
    """
    spark = _FakeSpark()
    assert live.abac_policies_unavailable(spark) is None
    assert "system.information_schema.abac_policy_definitions" in spark.ran[0]


def test_abac_relation_unavailable_gives_the_reason():
    """When it can't be read (Public Preview not enabled), the reason is returned for the notebook
    to warn (plan) or stop (apply) on.

    Expected: a reason naming the relation and the error's first line only.
    """
    spark = _FakeSpark(RuntimeError("TABLE_OR_VIEW_NOT_FOUND abac_policy_definitions\nstack..."))
    reason = live.abac_policies_unavailable(spark)
    assert reason.startswith("system.information_schema.abac_policy_definitions is not readable")
    assert "TABLE_OR_VIEW_NOT_FOUND" in reason and "stack" not in reason


def test_read_live_policies_no_longer_hides_a_failure():
    """read_live_policies raises when the relation fails — no silent "no live policies" (which
    would plan every policy as ADD and never DROP one).

    Expected: the RuntimeError propagates.
    """
    spark = _FakeSpark(RuntimeError("TABLE_OR_VIEW_NOT_FOUND"))
    with pytest.raises(RuntimeError, match="TABLE_OR_VIEW_NOT_FOUND"):
        live.read_live_policies(spark, ["hera-dev"])
