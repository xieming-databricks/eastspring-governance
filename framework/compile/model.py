"""Normalized target-state rows produced by the compiler.

All rows are frozen + ordered so target state is a set that can be diffed
deterministically against live state. ``securable_type`` values are normalized to
the grain that the corresponding ``information_schema`` view keys on (a view's
grants/tags land under ``table``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class TargetGrant:
    """One native privilege grant. Maps to information_schema.*_privileges."""
    principal: str          # account group (grantee)
    privilege_type: str     # e.g. "SELECT", "USE CATALOG", "MODIFY", "MANAGE", "EXECUTE"
    securable_type: str     # catalog | schema | table | function | volume
    securable_fqn: str


@dataclass(frozen=True, order=True)
class TargetTag:
    """One governed tag assignment. Maps to information_schema.*_tags."""
    securable_type: str     # catalog | schema | table | column | volume
    securable_fqn: str
    tag_key: str
    tag_value: str


@dataclass(frozen=True, order=True)
class TargetPolicy:
    """One ABAC policy. Compared against SHOW POLICIES output.

    ``policy_name`` is derived deterministically from (scope, kind, match signature)
    so it is stable when only ``function`` changes — enabling CHANGE detection.
    """
    scope: str              # catalog | schema | table fqn the policy is ON
    policy_name: str        # acm_policy_*
    policy_type: str        # "column_mask" (row filters retired; RLS is view-based)
    function: str           # fully-qualified function (the mutable "value")
    match_signature: str = field(compare=False)   # human-readable match detail


@dataclass(frozen=True, order=True)
class RefObject:
    """An object/function the config references, for the preflight existence check."""
    object_type: str        # catalog | schema | table | view | function | volume | column
    fqn: str
    origin: str = field(default="", compare=False)   # where referenced (context only)


@dataclass
class TargetState:
    """Everything the compiler produces from one config."""
    grants: list[TargetGrant] = field(default_factory=list)
    tags: list[TargetTag] = field(default_factory=list)
    policies: list[TargetPolicy] = field(default_factory=list)
    refs: list[RefObject] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # compile-time errors (halt if non-empty)

    @property
    def ok(self) -> bool:
        return not self.errors
