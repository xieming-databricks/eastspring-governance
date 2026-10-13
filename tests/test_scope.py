"""Tests for the protected-set assembly (full-sync guardrail)."""
from __future__ import annotations

import pytest

from eastspring_uc_governance.reconcile import scope


def test_protected_principals_unions_all_layers():
    """The protected-principal set combines the deploying identity, the reference's deployers and
    its admin groups.

    Expected: the set {sp@x, deploy-grp, account admins}.
    """
    got = scope.protected_principals(
        deployer="sp@x", deployers=["deploy-grp"], admin_groups=["account admins"])
    assert got == {"sp@x", "deploy-grp", "account admins"}


def test_protected_principals_empty_when_nothing_given():
    """The protected-principal set is empty when no deployer, deployers or admin groups are given.

    Expected: an empty set.
    """
    assert scope.protected_principals() == set()


def test_protected_principals_ignores_blank_and_none():
    """Blank strings, whitespace-only entries and None are dropped from the protected-principal set.

    Expected: only {grp} remains.
    """
    got = scope.protected_principals(deployer="", deployers=["", "  ", "grp"], admin_groups=None)
    assert got == {"grp"}


def test_protected_schemas_always_includes_information_schema():
    """The protected-schema set always contains information_schema and adds any extra schemas from
    the reference.

    Expected: {information_schema} with no extras, and {information_schema, staging} with
    extra=[staging].
    """
    assert scope.protected_schemas() == {"information_schema"}
    assert scope.protected_schemas(extra=["staging"]) == {"information_schema", "staging"}


# ---- managed grant scope (skip_base_grants) --------------------------------

def test_managed_grant_schemas():
    """With skip_base_grants true, the managed grant scope is the serving schemas under
    target_catalog, as bare names.

    Expected: [dm_esg, dm_other] (elsewhere.dm_x is dropped as another catalog); [] when no serving
    schemas are declared.
    """
    ref = {"skip_base_grants": True,
           "serving_schemas": ["cat.dm_esg", "cat.dm_other", "elsewhere.dm_x"]}
    assert scope.managed_grant_schemas(ref, "cat") == ["dm_esg", "dm_other"]   # only target_catalog
    assert scope.managed_grant_schemas({"skip_base_grants": True}, "cat") == []          # no serving schemas


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_managed_grant_schemas_whole_catalog_unless_real_true(value):
    """For each non-true value of skip_base_grants (False, the string "true", 1, None), base grants
    stay managed across the whole catalog.

    Expected: managed_grant_schemas returns None (whole catalog) for each value, and also for an
    empty or None reference.
    """
    # only a real boolean `true` skips base grants; anything else keeps the whole-catalog scope
    ref = {"skip_base_grants": value, "serving_schemas": ["cat.dm_esg"]}
    assert scope.managed_grant_schemas(ref, "cat") is None
    assert scope.managed_grant_schemas({}, "cat") is None and scope.managed_grant_schemas(None, "cat") is None
