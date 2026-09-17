"""Compile validated config dicts into normalized target state.

``compile_config(docs)`` assumes ``docs`` already passed ``validate_docs``. It:
  1. indexes asset LABELS (labels.yaml) for grant matching — labels are config-only
     and are NEVER materialized to UC,
  2. indexes column TAG assignments (tags.yaml) as the desired (materialized) tag state,
  3. for each grant rule, selects objects = label-matched (by object_type + labels)
     + explicit includes - explicit excludes, then bundle-expands per group,
  4. normalizes ABAC policies,
  5. collects referenced objects/functions for the preflight existence check.

Grant object-matching uses the DESIRED labels in ``labels.yaml`` (config-only,
deterministic) — never live tags. Tags in ``tags.yaml`` are column-routing tags
(acm_pii_type / acm_rls) that DO get materialized.
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
    base_grants = _doc(docs, "base_grants")
    rbac_view_grants = _doc(docs, "rbac_view_grants")
    labels_doc = _doc(docs, "labels")
    tags_doc = _doc(docs, "tags")
    policies_doc = _doc(docs, "policies")

    # --- labels: config-only asset labels for grant matching -------------
    # Labels are NEVER materialized to UC — unlike tags, they do not become target
    # state; they only select securables for grant rules.
    label_index: list[tuple[str, str, dict]] = []   # (securable_type, fqn, labels)
    for entry in labels_doc.get("labels", []):
        sec = entry["securable"]
        st, fqn, lb = sec["type"], sec["name"], entry["labels"]
        label_index.append((st, fqn, lb))
        state.refs.append(RefObject(st, fqn, "label"))

    # --- tags: desired column-tag assignments (materialized to UC) -------
    for entry in tags_doc.get("tags", []):
        sec = entry["securable"]
        st, fqn, tg = sec["type"], sec["name"], entry["tags"]
        state.refs.append(RefObject(st, fqn, "tag"))
        for k, v in tg.items():
            state.tags.append(TargetTag(st, fqn, k, v))

    # --- grants: base (base_grants.yaml) + serving-schema (rbac_grants.yaml) ---
    # Both are grant_rules and compile identically (bundle-expand into TargetGrants).
    grant_set: set[TargetGrant] = set()
    all_rules = base_grants.get("grant_rules", []) + rbac_view_grants.get("grant_rules", [])
    for i, rule in enumerate(all_rules):
        groups = rule["groups"]
        bundle = rule["bundle"]
        objs: list[tuple[str, str]] = []

        match = rule.get("match")
        if match:
            want_types = set(match["object_types"])
            want_labels = match.get("labels", {})
            for st, fqn, lb in label_index:
                if st in want_types and all(lb.get(k) == v for k, v in want_labels.items()):
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
