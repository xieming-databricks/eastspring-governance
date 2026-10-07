"""Plan group + schema provisioning from the account inventory / env reference (pure Python).

Each planner compares what the YAML declares against what exists live and returns one ``Action``
per declared object:

- ``plan_groups(account, live_groups)``  — config/account/groups.yaml
    ``external_id: null``   (local): EXISTS | CREATE
    ``external_id: <GUID>`` (AIM):   EXISTS | RESOLVE | CONFLICT
- ``plan_schemas(reference, live_schemas)`` — config/reference/<env>.yaml
    governance + serving schemas under target_catalog: EXISTS | CREATE

Additive only: an object that exists live but is no longer declared is never touched (unlike
``terraform/``, which drops what is removed from the YAML).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXISTS = "EXISTS"        # already there — nothing to do
CREATE = "CREATE"        # create it (schema / local group)
RESOLVE = "RESOLVE"      # AIM: resolve the Entra group by external_id (provisions it)
CONFLICT = "CONFLICT"    # needs a human: not executed, fails the run


@dataclass(frozen=True)
class Action:
    kind: str                   # "group" | "schema"
    target: str                 # group name or schema FQN
    status: str                 # EXISTS | CREATE | RESOLVE | CONFLICT
    argv: tuple[str, ...] = ()  # databricks CLI arguments (empty unless CREATE / RESOLVE)
    external_id: str | None = None
    note: str = ""


# ---- groups (account level) ------------------------------------------------

def desired_groups(account: dict[str, Any]) -> list[tuple[str, str | None]]:
    """``[(name, external_id | None)]`` from the inventory's ``groups`` list, in order. Entries
    without a name are skipped and a repeated name keeps its first entry (the validator warns)."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for entry in account.get("groups") or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name or name in seen:
            continue
        seen.add(name)
        ext = entry.get("external_id")
        out.append((name, str(ext) if ext is not None else None))
    return out


def plan_groups(account: dict[str, Any], live_groups: list[dict[str, Any]]) -> list[Action]:
    """One ``Action`` per inventory group. ``live_groups`` are the account's SCIM groups
    (``displayName`` / ``externalId``)."""
    by_name = {g.get("displayName"): g for g in live_groups if g.get("displayName")}
    by_ext = {str(g["externalId"]).lower(): g for g in live_groups if g.get("externalId")}
    actions: list[Action] = []
    for name, ext in desired_groups(account):
        if ext is None:                                       # local group
            if name in by_name:
                actions.append(Action("group", name, EXISTS))
            else:
                actions.append(Action("group", name, CREATE,
                                      ("account", "groups", "create", "--display-name", name)))
            continue
        linked = by_ext.get(ext.lower())                      # AIM group
        if linked is not None:
            live_name = linked.get("displayName")
            if live_name == name:
                actions.append(Action("group", name, EXISTS, external_id=ext))
            else:
                actions.append(Action("group", name, CONFLICT, external_id=ext,
                                      note=f"external_id is already provisioned as group "
                                           f"{live_name!r} — the GUID or the name is wrong"))
        elif name in by_name:
            actions.append(Action("group", name, CONFLICT, external_id=ext,
                                  note="a group with this name already exists without this "
                                       "external_id (e.g. a local group) — delete it first"))
        else:
            actions.append(Action("group", name, RESOLVE,
                                  ("account", "iam-v2", "resolve-group", ext), external_id=ext))
    return actions


# ---- schemas (per env) -----------------------------------------------------

def desired_schemas(reference: dict[str, Any]) -> list[str]:
    """governance_schemas + serving_schemas FQNs under target_catalog (source_schemas are
    DE-owned base data, not provisioned). Same selection as ``terraform/workspace/main.tf``."""
    catalog = reference.get("target_catalog") or ""
    wanted = list(reference.get("governance_schemas") or []) + list(reference.get("serving_schemas") or [])
    return sorted({s for s in wanted if catalog and s.startswith(f"{catalog}.")})


def plan_schemas(reference: dict[str, Any], live_schemas: set[str], *,
                 source: str = "config/reference") -> list[Action]:
    """One ``Action`` per declared schema; ``live_schemas`` are the FQNs visible in the catalog."""
    actions: list[Action] = []
    for fqn in desired_schemas(reference):
        catalog, name = fqn.split(".", 1)
        if fqn in live_schemas:
            actions.append(Action("schema", fqn, EXISTS))
        else:
            actions.append(Action("schema", fqn, CREATE,
                                  ("schemas", "create", name, catalog,
                                   "--comment", f"Provisioned from {source}")))
    return actions
