"""Read live state and execute provisioning ``Action``s through the ``databricks`` CLI.

Every call is ``databricks <argv> -o json [--profile P]`` in a subprocess, always with a WORKSPACE
profile — groups go through the workspace-level IAM v2 proxy API, so no account-level access is
needed. ``execute`` keeps going when one action fails (continue-and-report, as in
``eastspring_uc_governance.apply.executor``).
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .planner import CONFLICT, CREATE, EXISTS, RESOLVE, UPDATE, Action


class CliError(RuntimeError):
    """A ``databricks`` CLI call exited non-zero (message carries the CLI's stderr)."""


def run(argv: tuple[str, ...] | list[str], profile: str | None = None) -> Any:
    """Run one CLI command and return its parsed JSON output (None if it printed nothing)."""
    cmd = ["databricks", *argv, "-o", "json"] + (["--profile", profile] if profile else [])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CliError((proc.stderr or proc.stdout).strip() or f"exit code {proc.returncode}")
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def list_account_groups(profile: str | None = None) -> list[dict[str, Any]]:
    """All groups in the account that parents the workspace (group_id / group_name / external_id),
    via the workspace-level proxy API — a WORKSPACE profile, no account-level access needed."""
    return run(("workspace-iam-v2", "list-groups-proxy"), profile) or []


def list_schemas(catalog: str, profile: str | None = None) -> set[str]:
    """FQNs of the schemas in ``catalog`` the caller can see. A non-admin caller only sees schemas
    it owns / has USE_SCHEMA on, so a missing entry may still exist — ``execute`` handles that."""
    return {s["full_name"] for s in run(("schemas", "list", catalog), profile) or [] if s.get("full_name")}


def list_tag_policies(profile: str | None = None) -> list[dict[str, Any]]:
    """All governed tags (tag policies) in the account: tag_key / description / values [{name}].
    Account-wide, but read through the workspace API — a WORKSPACE profile."""
    return run(("tag-policies", "list-tag-policies"), profile) or []


def _resolved_name(resp: Any) -> str | None:
    # resolve-group-proxy is expected to return {"group": {...}} like the account-level resolve; a
    # bare group object is tolerated too (neither shape is confirmed against a live AIM call yet).
    if not isinstance(resp, dict):
        return None
    group = resp.get("group") if isinstance(resp.get("group"), dict) else resp
    return group.get("group_name") or group.get("display_name")


def execute(actions: list[Action], *, profile: str | None = None,
            dry_run: bool = True) -> list[dict[str, Any]]:
    """Run (or preview) the actions. One result per action, status: EXISTS | PLANNED (dry-run) |
    SUCCEEDED | FAILED | CONFLICT (never executed; fails the run like FAILED). A CONFLICT's note
    is its ``error``; any other note is information (``note``)."""
    results: list[dict[str, Any]] = []
    for a in actions:
        result = {"kind": a.kind, "target": a.target,
                  "command": " ".join(("databricks",) + a.argv) if a.argv else "",
                  "status": a.status,
                  "error": a.note if a.status == CONFLICT and a.note else None,
                  "note": a.note if a.status != CONFLICT else ""}
        if a.status in (CREATE, UPDATE, RESOLVE):
            result["status"] = "PLANNED"
            if not dry_run:
                try:
                    resp = run(a.argv, profile)
                    result["status"] = "SUCCEEDED"
                    if a.status == RESOLVE and _resolved_name(resp) != a.target:
                        # resolve has already provisioned *something* — report it loudly.
                        result["status"] = "FAILED"
                        result["error"] = (f"external_id {a.external_id} resolved to group "
                                           f"{_resolved_name(resp)!r}, not {a.target!r} — it IS now "
                                           "provisioned in the account; fix the GUID/name")
                except CliError as exc:
                    if a.kind == "schema" and "already exists" in str(exc).lower():
                        result["status"] = EXISTS     # existed but wasn't visible to the plan
                    else:
                        result["status"] = "FAILED"
                        result["error"] = str(exc)
        results.append(result)
    return results


def failed(results: list[dict[str, Any]]) -> bool:
    return any(r["status"] in ("FAILED", CONFLICT) for r in results)
