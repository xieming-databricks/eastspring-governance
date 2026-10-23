"""Plan group, schema + governed-tag provisioning from the env reference allow-list (pure Python).

Each planner compares what the YAML declares against what exists live and returns one ``Action``
per declared object:

- ``plan_groups(reference, live_groups)``  — config/reference/<env>.yaml ``groups`` (via the
  workspace proxy API)
    ``external_id: null``   (local): EXISTS | CREATE
    ``external_id: <GUID>`` (AIM):   EXISTS | RESOLVE | CONFLICT
- ``plan_schemas(reference, live_schemas)`` — config/reference/<env>.yaml
    governance + serving schemas under target_catalog: EXISTS | CREATE
- ``plan_governed_tags(reference, live_tag_policies)`` — config/reference/<env>.yaml
    ``governed_tags`` (tag key -> allowed values) as tag policies: EXISTS | CREATE | UPDATE

Additive only: an object that exists live but is no longer declared is never touched — removing
it from the YAML deletes nothing. For governed tags that also means a
value is only ever added to a tag's allowed list, never removed, and a tag that accepts any value
is never narrowed to a list — either could break existing tag assignments and the ABAC policies
that match on them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

EXISTS = "EXISTS"        # already there — nothing to do
CREATE = "CREATE"        # create it (schema / local group / governed tag)
UPDATE = "UPDATE"        # governed tag: add the missing allowed values
RESOLVE = "RESOLVE"      # AIM: resolve the Entra group by external_id (provisions it)
CONFLICT = "CONFLICT"    # needs a human: not executed, fails the run


@dataclass(frozen=True)
class Action:
    kind: str                   # "group" | "schema" | "tag"
    target: str                 # group name, schema FQN or governed tag key
    status: str                 # EXISTS | CREATE | UPDATE | RESOLVE | CONFLICT
    argv: tuple[str, ...] = ()  # databricks CLI arguments (empty unless CREATE / UPDATE / RESOLVE)
    external_id: str | None = None
    note: str = ""              # CONFLICT: why; otherwise extra information for the report


# ---- groups (account groups, provisioned per env) ---------------------------

def desired_groups(reference: dict[str, Any]) -> list[tuple[str, str | None]]:
    """``[(name, external_id | None)]`` from the reference's ``groups`` list, in order. Entries
    without a name are skipped and a repeated name keeps its first entry (the validator warns)."""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for entry in reference.get("groups") or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name or name in seen:
            continue
        seen.add(name)
        ext = entry.get("external_id")
        out.append((name, str(ext) if ext is not None else None))
    return out


def plan_groups(reference: dict[str, Any], live_groups: list[dict[str, Any]]) -> list[Action]:
    """One ``Action`` per declared group. ``live_groups`` are the account's groups as returned by
    ``workspace-iam-v2 list-groups-proxy`` (``group_name`` / ``external_id``).

    Every command goes through the workspace-level IAM v2 *proxy* API, which acts on the account
    that parents the workspace — so this needs a workspace admin, never account-level access."""
    by_name = {g.get("group_name"): g for g in live_groups if g.get("group_name")}
    by_ext = {str(g["external_id"]).lower(): g for g in live_groups if g.get("external_id")}
    actions: list[Action] = []
    for name, ext in desired_groups(reference):
        if ext is None:                                       # local group
            if name in by_name:
                actions.append(Action("group", name, EXISTS))
            else:
                actions.append(Action("group", name, CREATE,
                                      ("workspace-iam-v2", "create-group-proxy", "--group-name", name)))
            continue
        linked = by_ext.get(ext.lower())                      # AIM group
        if linked is not None:
            live_name = linked.get("group_name")
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
                                  ("workspace-iam-v2", "resolve-group-proxy", ext), external_id=ext))
    return actions


# ---- schemas (per env) -----------------------------------------------------

def desired_schemas(reference: dict[str, Any]) -> list[str]:
    """governance_schemas + serving_schemas FQNs under target_catalog (source_schemas are
    DE-owned base data, not provisioned)."""
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


# ---- governed tags (tag policies; account-wide, via the workspace API) -----

def desired_governed_tags(reference: dict[str, Any]) -> list[tuple[str, list[str] | None]]:
    """``[(tag_key, allowed values | None)]`` from the reference's ``governed_tags``, in order.
    ``None`` (a null list) means any value is allowed — the validator doesn't value-check it
    either. Values are de-duplicated, keeping their order."""
    governed = reference.get("governed_tags")
    if not isinstance(governed, dict):
        return []
    out: list[tuple[str, list[str] | None]] = []
    for key, values in governed.items():
        if values is None:
            out.append((str(key), None))
        else:
            out.append((str(key), list(dict.fromkeys(str(v) for v in values))))
    return out


def _tag_values(values: list[str]) -> list[dict[str, str]]:
    return [{"name": v} for v in values]


def plan_governed_tags(reference: dict[str, Any], live_tag_policies: list[dict[str, Any]], *,
                       source: str = "config/reference") -> list[Action]:
    """One ``Action`` per declared governed tag. ``live_tag_policies`` are the account's tag
    policies as returned by ``tag-policies list-tag-policies`` (``tag_key`` / ``values: [{name}]``;
    no ``values`` = the tag accepts any value).

      missing                          -> CREATE  (the reference's values, if it lists any)
      live list lacks reference values -> UPDATE  (live values + the missing ones, live order kept)
      otherwise                        -> EXISTS
    Values that are live but not in the reference are kept and only reported (``note``)."""
    live = {p["tag_key"]: p for p in live_tag_policies if p.get("tag_key")}
    actions: list[Action] = []
    for key, values in desired_governed_tags(reference):
        policy = live.get(key)
        if policy is None:
            body: dict[str, Any] = {"tag_key": key, "description": f"Provisioned from {source}"}
            if values:
                body["values"] = _tag_values(values)
            actions.append(Action("tag", key, CREATE,
                                  ("tag-policies", "create-tag-policy", "--json", json.dumps(body)),
                                  note=f"allowed values: {', '.join(values)}" if values
                                  else "any value allowed"))
            continue
        live_values = [v["name"] for v in policy.get("values") or [] if v.get("name")]
        if not live_values:                                   # accepts any value: never narrowed
            note = (f"accepts any value in Databricks — not narrowed to the reference's "
                    f"{len(values)} value(s)" if values else "")
            actions.append(Action("tag", key, EXISTS, note=note))
            continue
        if values is None:
            actions.append(Action("tag", key, EXISTS,
                                  note=f"reference allows any value, Databricks only: "
                                       f"{', '.join(live_values)} — left as is"))
            continue
        missing = [v for v in values if v not in live_values]
        extra = [v for v in live_values if v not in values]
        notes = ([f"add: {', '.join(missing)}"] if missing else []) + \
                ([f"in Databricks, not in reference: {', '.join(extra)} — left as is"] if extra else [])
        if missing:
            actions.append(Action("tag", key, UPDATE,
                                  ("tag-policies", "update-tag-policy", key, "values", "--json",
                                   json.dumps({"values": _tag_values(live_values + missing)})),
                                  note="; ".join(notes)))
        else:
            actions.append(Action("tag", key, EXISTS, note="; ".join(notes)))
    return actions
