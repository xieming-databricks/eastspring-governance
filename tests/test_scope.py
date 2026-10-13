"""Tests for the protected-set assembly (full-sync guardrail)."""
from __future__ import annotations

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
