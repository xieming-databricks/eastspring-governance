"""Tests for the protected-set assembly (full-sync guardrail)."""
from __future__ import annotations

import pytest

from eastspring_uc_governance.reconcile import scope


def test_protected_principals_unions_all_layers():
    got = scope.protected_principals(
        deployer="sp@x", deployers=["deploy-grp"], admin_groups=["account admins"])
    assert got == {"sp@x", "deploy-grp", "account admins"}


def test_protected_principals_empty_when_nothing_given():
    assert scope.protected_principals() == set()


def test_protected_principals_ignores_blank_and_none():
    got = scope.protected_principals(deployer="", deployers=["", "  ", "grp"], admin_groups=None)
    assert got == {"grp"}


def test_protected_schemas_always_includes_information_schema():
    assert scope.protected_schemas() == {"information_schema"}
    assert scope.protected_schemas(extra=["staging"]) == {"information_schema", "staging"}


# ---- managed grant scope (skip_base_grants) --------------------------------

def test_managed_grant_schemas():
    ref = {"skip_base_grants": True,
           "serving_schemas": ["cat.dm_esg", "cat.dm_other", "elsewhere.dm_x"]}
    assert scope.managed_grant_schemas(ref, "cat") == ["dm_esg", "dm_other"]   # only target_catalog
    assert scope.managed_grant_schemas({"skip_base_grants": True}, "cat") == []          # no serving schemas


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_managed_grant_schemas_whole_catalog_unless_real_true(value):
    # only a real boolean `true` skips base grants; anything else keeps the whole-catalog scope
    ref = {"skip_base_grants": value, "serving_schemas": ["cat.dm_esg"]}
    assert scope.managed_grant_schemas(ref, "cat") is None
    assert scope.managed_grant_schemas({}, "cat") is None and scope.managed_grant_schemas(None, "cat") is None
