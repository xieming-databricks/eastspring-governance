"""Tests for the preflight existence check."""
from __future__ import annotations

from eastspring_uc_governance.compile.model import RefObject
from eastspring_uc_governance.reconcile import preflight


def test_missing_object_and_function_warn():
    """Preflight flags referenced objects (a table and a mask function) that do not exist live,
    while an existing table passes.

    Expected: exactly two warnings: table c.s.missing and function c.g.absent_fn; the present table
    c.s.present is not flagged.
    """
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
    """Preflight reports nothing when every referenced catalog and schema exists live.

    Expected: an empty warning list.
    """
    refs = [RefObject("catalog", "c", "x"), RefObject("schema", "c.s", "x")]
    existing = {("catalog", "c"), ("schema", "c.s")}
    assert preflight.check_existence(refs, existing) == []


def test_view_table_leniency():
    """Preflight treats view and table as interchangeable, since config may call an object a view
    while live state lists it as a table (or vice versa).

    Expected: an empty warning list for both cross-typed references.
    """
    # config referenced a view; live surfaced it under 'table' (or vice versa) -> no warning
    refs = [RefObject("view", "c.s.v", "grant_rules/0"), RefObject("table", "c.s.t", "tag")]
    existing = {("table", "c.s.v"), ("view", "c.s.t")}
    assert preflight.check_existence(refs, existing) == []


def test_dedup_repeated_refs():
    """Preflight de-duplicates a missing object that is referenced twice from different places.

    Expected: exactly one warning for c.s.missing.
    """
    refs = [RefObject("table", "c.s.missing", "a"), RefObject("table", "c.s.missing", "b")]
    warns = preflight.check_existence(refs, set())
    assert len(warns) == 1
