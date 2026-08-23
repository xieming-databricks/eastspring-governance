"""Compile validated config dicts into normalized target state.

``compile_config(docs)`` assumes ``docs`` already passed ``validate_docs``. It:
  1. indexes tag assignments (the desired tag state),
  2. for each grant rule, selects objects = tag-matched (by object_type + acm_* tags)
     + explicit includes - explicit excludes, then bundle-expands per group,
  3. normalizes tags and ABAC policies,
  4. collects referenced objects/functions for the preflight existence check.

Grant object-matching uses the DESIRED tags in ``tags.yaml`` (config-only,
deterministic) — never live tags.
"""
from __future__ import annotations

from typing import Any

from .bundles import BundleError, expand_bundle
from .model import RefObject, TargetGrant, TargetPolicy, TargetState, TargetTag
from .naming import policy_name

def _doc(docs: dict[str, Any], name: str) -> dict:
    return docs.get(name) or {}


def _scope_type(scope: str) -> str:
    """Infer securable grain of a policy scope from its dotted depth."""
    n = scope.count(".")
    return {0: "catalog", 1: "schema", 2: "table"}.get(n, "table")


def compile_config(docs: dict[str, Any]) -> TargetState:
    state = TargetState()
    grants_doc = _doc(docs, "grants")
    tags_doc = _doc(docs, "tags")
    policies_doc = _doc(docs, "policies")

    # --- tags: desired assignments + index for grant matching ------------
    tag_index: list[tuple[str, str, dict]] = []   # (securable_type, fqn, tags)
    for entry in tags_doc.get("tags", []):
        sec = entry["securable"]
        st, fqn, tg = sec["type"], sec["name"], entry["tags"]
        tag_index.append((st, fqn, tg))
        state.refs.append(RefObject(st, fqn, "tag"))
        for k, v in tg.items():
            state.tags.append(TargetTag(st, fqn, k, v))

    # --- grants: match -> include -> exclude -> bundle-expand ------------
    grant_set: set[TargetGrant] = set()
    for i, rule in enumerate(grants_doc.get("grant_rules", [])):
        groups = rule["groups"]
        bundle = rule["bundle"]
        objs: list[tuple[str, str]] = []

        match = rule.get("match")
        if match:
            want_types = set(match["object_types"])
            want_tags = match.get("tags", {})
            for st, fqn, tg in tag_index:
                if st in want_types and all(tg.get(k) == v for k, v in want_tags.items()):
                    objs.append((st, fqn))
        for inc in rule.get("include", []):
            objs.append((inc["type"], inc["name"]))
        excl = {(e["type"], e["name"]) for e in rule.get("exclude", [])}
        objs = [o for o in dict.fromkeys(objs) if o not in excl]   # de-dup, drop excludes

        for ot, fqn in objs:
            state.refs.append(RefObject(ot, fqn, f"grant_rules/{i}"))
            try:
                privs = expand_bundle(bundle, ot, fqn)
            except BundleError as exc:
                state.errors.append(f"grant_rules/{i}: {exc}")
                continue
            for group in groups:
                for priv, sec_type, sec_fqn in privs:
                    grant_set.add(TargetGrant(group, priv, sec_type, sec_fqn))
    state.grants = sorted(grant_set)

    # --- policies: masks + row filters -----------------------------------
    for p in policies_doc.get("column_mask_policies", []):
        scope, fn = p["scope"], p["function"]
        tmt = p.get("table_matching_tags", {})
        pii = p["column_matching_tags"]["acm_pii_type"]
        sig = f"tmt={sorted(tmt.items())}|pii={pii}"
        name = policy_name("mask", scope, sig, descriptor=pii)
        state.policies.append(TargetPolicy(scope, name, "column_mask", fn, sig))
        state.refs.append(RefObject("function", fn, "policy.function"))
        state.refs.append(RefObject(_scope_type(scope), scope, "policy.scope"))

    for p in policies_doc.get("row_filter_policies", []):
        scope, fn = p["scope"], p["function"]
        tmt = p.get("table_matching_tags", {})
        cms = p["column_matching_tags"]
        dims = [c["condition"]["acm_rls"] for c in cms]
        sig = "tmt={}|rls={}".format(
            sorted(tmt.items()),
            ";".join(f"{c['condition']['acm_rls']}:{c['as']}" for c in cms),
        )
        name = policy_name("rls", scope, sig, descriptor="_".join(dims))
        state.policies.append(TargetPolicy(scope, name, "row_filter", fn, sig))
        state.refs.append(RefObject("function", fn, "policy.function"))
        state.refs.append(RefObject(_scope_type(scope), scope, "policy.scope"))

    # de-dup / order the derived collections
    state.tags = sorted(set(state.tags))
    state.policies = sorted(set(state.policies))
    state.refs = sorted(set(state.refs))
    return state
