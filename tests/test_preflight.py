"""Tests for the preflight existence check."""
from __future__ import annotations

from framework.compile.model import RefObject
from framework.reconcile import preflight


def test_missing_object_and_function_warn():
    refs = [
        RefObject("table", "c.s.present", "tag"),
        RefObject("table", "c.s.missing", "grant_rules/0"),
        RefObject("function", "c.g.absent_fn", "policy.function"),
    ]
    existing = {("table", "c.s.present")}
    warns = preflight.check_existence(refs, existing)
    missing = {(w["object_type"], w["fqn"]) for w in warns}
    assert missing == {("table", "c.s.missing"), ("function", "c.g.absent_fn")}


def test_all_present_no_warnings():
    refs = [RefObject("catalog", "c", "x"), RefObject("schema", "c.s", "x")]
    existing = {("catalog", "c"), ("schema", "c.s")}
    assert preflight.check_existence(refs, existing) == []


def test_view_table_leniency():
    # config referenced a view; live surfaced it under 'table' (or vice versa) -> no warning
    refs = [RefObject("view", "c.s.v", "grant_rules/0"), RefObject("table", "c.s.t", "tag")]
    existing = {("table", "c.s.v"), ("view", "c.s.t")}
    assert preflight.check_existence(refs, existing) == []


def test_dedup_repeated_refs():
    refs = [RefObject("table", "c.s.missing", "a"), RefObject("table", "c.s.missing", "b")]
    warns = preflight.check_existence(refs, set())
    assert len(warns) == 1
