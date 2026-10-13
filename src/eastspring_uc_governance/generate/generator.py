"""Pure transform: Access-Matrix workbook rows -> framework config docs.

``generate_config(wb)`` maps the workbook tabs (as plain row-dicts from ``read_workbook``)
into the docs-shaped dict the framework already understands
(``{"grants":…, "labels":…, "tags":…, "policies":…}``) — the same shape
``validation.validate_docs`` and ``compile.compile_config`` consume. No Spark, no Excel here,
so it is unit-testable with plain dicts.

v3 dynamic-views layout: ``labels.yaml`` from Base Tables (object selection); ``grants.yaml`` from
Base Grant Rules (base-table grants — e.g. admin/owner) + Rbac Views (end-user grants on the serving
SCHEMA). ``tags.yaml`` from Asset Classification (column pii tags); ``policies.yaml`` column masks
from Masking Rules (``when_key``/``when_value`` table-narrowing dropped for now); ``rbac_views.yaml``
(expected dynamic views) from Base Tables' ``target_schema`` / ``rls_column``.
The workbook's ``data_product`` values are free strings in the schema, so their approved set is
enforced HERE (against the Data Products tab) — that was the deal when we chose open-string
labels. Any referential problem raises :class:`GeneratorError` listing every problem; nothing
is written on failure. ``generate_config`` also runs ``validate_docs`` on its own output and
refuses to return an invalid config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from eastspring_uc_governance.validation import validate_docs

SCHEMA_VERSION = "1.0"
KNOWN_BUNDLES = frozenset({"READ", "WRITE", "OWNER", "ADMIN"})
LABEL_KEYS = frozenset({"business_function", "data_product"})
OBJECT_TYPES = frozenset({"catalog", "schema", "table", "view", "function", "volume"})

# Workbook tabs this generator consumes (v3 dynamic-views layout).
T_GROUPS = "Groups"
T_DATA_PRODUCTS = "Data Products"
T_TABLES = "Base Tables"
T_GRANT_RULES = "Base Grant Rules"
T_GRANT_EXCEPTIONS = "Base Grant Exceptions"
T_PERMISSION_BUNDLES = "Permission Bundles"
T_RBAC_VIEWS = "Rbac Views"
T_ASSET_CLASSIFICATION = "Asset Classification"
T_MASKING_RULES = "Masking Rules"
REQUIRED_SHEETS = (T_GROUPS, T_DATA_PRODUCTS, T_TABLES, T_GRANT_RULES,
                   T_GRANT_EXCEPTIONS, T_PERMISSION_BUNDLES, T_RBAC_VIEWS,
                   T_ASSET_CLASSIFICATION, T_MASKING_RULES)

Docs = dict[str, dict[str, Any]]


class GeneratorError(ValueError):
    """Raised when the workbook cannot be converted to a valid config (lists all problems)."""


def _parse_object_types(raw: Any) -> list[str]:
    """'table; view' -> ['table', 'view'] (split on ';', trim, drop empties)."""
    return [p.strip() for p in str(raw or "").split(";") if p.strip()]


def _column(rows: list[dict], key: str) -> set:
    """Non-empty values of one column, as a set (for membership checks)."""
    return {r[key] for r in rows if r.get(key)}


def generate_config(wb: dict[str, list[dict[str, Any]]]) -> Docs:
    """Transform workbook row-dicts into the docs dict; raise ``GeneratorError`` on any problem."""
    problems: list[str] = []

    groups = _column(wb.get(T_GROUPS, []), "group_name")
    data_products = _column(wb.get(T_DATA_PRODUCTS, []), "data_product")
    bundles = _column(wb.get(T_PERMISSION_BUNDLES, []), "bundle")
    rule_ids = _column(wb.get(T_GRANT_RULES, []), "rule_id")

    # rule_id identifies a rule (and is how exceptions attach), so it must be unique.
    _rids = [r["rule_id"] for r in wb.get(T_GRANT_RULES, []) if r.get("rule_id")]
    for dup in sorted({x for x in _rids if _rids.count(x) > 1}):
        problems.append(f"'{T_GRANT_RULES}' tab: duplicate rule_id {dup!r}")

    # ---- labels.yaml + views.yaml from the Base Tables tab ---------------
    # labels: one per securable (merged). views: one per (base table -> target_schema)
    # routing, so a base table feeding multiple serving schemas yields multiple views.
    labels_by_sec: dict[tuple[str, str], dict[str, str]] = {}
    views: list[dict[str, Any]] = []
    for i, r in enumerate(wb.get(T_TABLES, [])):
        cat, sch, obj = r.get("catalog"), r.get("schema"), r.get("object")
        otype, dp = r.get("object_type"), r.get("data_product")
        tsch, rcol = r.get("target_schema"), r.get("rls_column")
        if otype not in OBJECT_TYPES:
            problems.append(f"{T_TABLES} row {i}: object_type {otype!r} not one of {sorted(OBJECT_TYPES)}")
        if dp not in data_products:
            problems.append(f"{T_TABLES} row {i}: data_product {dp!r} not in '{T_DATA_PRODUCTS}' tab")
        if not (cat and sch and obj):
            problems.append(f"{T_TABLES} row {i}: missing catalog/schema/object")
            continue
        labels_by_sec.setdefault((otype, f"{cat}.{sch}.{obj}"), {})["data_product"] = dp
        if tsch:                                     # base table -> dynamic view in the serving schema
            v: dict[str, Any] = {"view": f"{tsch}.{obj}", "source": f"{cat}.{sch}.{obj}"}
            if rcol:
                v["rls_column"] = rcol
            views.append(v)
    labels_entries = [
        {"securable": {"type": t, "name": n}, "labels": lb}
        for (t, n), lb in sorted(labels_by_sec.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    ]
    views.sort(key=lambda v: v["view"])
    _vfqns = [v["view"] for v in views]
    for dup in sorted({x for x in _vfqns if _vfqns.count(x) > 1}):
        problems.append(f"'{T_TABLES}' tab: duplicate view target {dup!r} (same target_schema + object)")

    # ---- Grant Exceptions indexed by rule_id -> include / exclude --------
    exc_by_rule: dict[str, dict[str, list[dict]]] = {}
    for i, r in enumerate(wb.get(T_GRANT_EXCEPTIONS, [])):
        rid, action = r.get("rule_id"), r.get("action")
        otype, obj = r.get("object_type"), r.get("object")
        eid = r.get("exception_id")
        if rid not in rule_ids:
            problems.append(f"{T_GRANT_EXCEPTIONS} {eid}: rule_id {rid!r} not in '{T_GRANT_RULES}' tab")
        if action not in ("include", "exclude"):
            problems.append(f"{T_GRANT_EXCEPTIONS} {eid}: action {action!r} must be include/exclude")
            continue
        if otype not in OBJECT_TYPES:
            problems.append(f"{T_GRANT_EXCEPTIONS} {eid}: object_type {otype!r} invalid")
        entry: dict[str, Any] = {"type": otype, "name": obj}
        if eid:
            entry["exp_id"] = eid                       # trace back to the Access-Matrix exception
        exc_by_rule.setdefault(rid, {"include": [], "exclude": []})[action].append(entry)

    # ---- grants.yaml from the Grant Rules tab ----------------------------
    grant_rules: list[dict[str, Any]] = []
    for r in wb.get(T_GRANT_RULES, []):
        rid, gname = r.get("rule_id"), r.get("group_name")
        mlk, mlv, bundle = r.get("match_label_key"), r.get("match_label_value"), r.get("bundle")
        otypes = _parse_object_types(r.get("object_types"))
        if gname not in groups:
            problems.append(f"{T_GRANT_RULES} {rid}: group_name {gname!r} not in '{T_GROUPS}' tab")
        if bundle not in KNOWN_BUNDLES:
            problems.append(f"{T_GRANT_RULES} {rid}: bundle {bundle!r} not one of {sorted(KNOWN_BUNDLES)}")
        elif bundles and bundle not in bundles:
            problems.append(f"{T_GRANT_RULES} {rid}: bundle {bundle!r} not in '{T_PERMISSION_BUNDLES}' tab")
        if mlk not in LABEL_KEYS:
            problems.append(f"{T_GRANT_RULES} {rid}: match_label_key {mlk!r} not one of {sorted(LABEL_KEYS)}")
        if mlk == "data_product" and mlv not in data_products:
            problems.append(f"{T_GRANT_RULES} {rid}: match_label_value {mlv!r} not in '{T_DATA_PRODUCTS}' tab")
        if not otypes:
            problems.append(f"{T_GRANT_RULES} {rid}: object_types is empty")
        for ot in otypes:
            if ot not in OBJECT_TYPES:
                problems.append(f"{T_GRANT_RULES} {rid}: object_type {ot!r} invalid")

        rule: dict[str, Any] = {}
        if rid:
            rule["rule_id"] = rid                       # trace back to the Access-Matrix Grant Rule
        rule["groups"] = [gname]
        rule["bundle"] = bundle
        rule["match"] = {"object_types": otypes, "labels": {mlk: mlv}}
        exc = exc_by_rule.get(rid, {})
        if exc.get("include"):
            rule["include"] = exc["include"]
        if exc.get("exclude"):
            rule["exclude"] = exc["exclude"]
        grant_rules.append(rule)

    # ---- rbac_view_grants.yaml from the Rbac Views tab (serving-schema grants) -
    # End-user groups get their bundle on the serving SCHEMA (schema-grain include).
    # The views inside that schema are produced by view generation (not implemented here);
    # the schema itself is provisioned by Terraform.
    rbac_rules: list[dict[str, Any]] = []
    _rbac_ids = [r["rule_id"] for r in wb.get(T_RBAC_VIEWS, []) if r.get("rule_id")]
    for dup in sorted({x for x in _rbac_ids if _rbac_ids.count(x) > 1}):
        problems.append(f"'{T_RBAC_VIEWS}' tab: duplicate rule_id {dup!r}")
    for r in wb.get(T_RBAC_VIEWS, []):
        rid, gname = r.get("rule_id"), r.get("group_name")
        cat, sch, bundle = r.get("catalog"), r.get("schema"), r.get("bundle")
        if gname not in groups:
            problems.append(f"{T_RBAC_VIEWS} {rid}: group_name {gname!r} not in '{T_GROUPS}' tab")
        if bundle not in KNOWN_BUNDLES:
            problems.append(f"{T_RBAC_VIEWS} {rid}: bundle {bundle!r} not one of {sorted(KNOWN_BUNDLES)}")
        elif bundles and bundle not in bundles:
            problems.append(f"{T_RBAC_VIEWS} {rid}: bundle {bundle!r} not in '{T_PERMISSION_BUNDLES}' tab")
        if not (cat and sch):
            problems.append(f"{T_RBAC_VIEWS} {rid}: missing catalog/schema")
            continue
        vrule: dict[str, Any] = {}
        if rid:
            vrule["rule_id"] = rid
        vrule["groups"] = [gname]
        vrule["bundle"] = bundle
        vrule["include"] = [{"type": "schema", "name": f"{cat}.{sch}"}]
        rbac_rules.append(vrule)

    # ---- tags.yaml from the Asset Classification tab (column tags) --------
    # Materialized column tags. The workbook's tag_key is already acm_-namespaced (e.g.
    # acm_pii_type) and is used verbatim — no key translation. WHICH acm_ keys are legal is a
    # closed set owned by tags.schema.yaml and enforced by the self-check below, so the generator
    # keeps no separate tag-key list. Merged per column securable.
    tags_by_col: dict[str, dict[str, str]] = {}
    for i, r in enumerate(wb.get(T_ASSET_CLASSIFICATION, [])):
        obj, tkey, tval = r.get("object"), r.get("tag_key"), r.get("tag_value")
        if not obj:
            problems.append(f"{T_ASSET_CLASSIFICATION} row {i}: missing object")
            continue
        if not tkey:
            problems.append(f"{T_ASSET_CLASSIFICATION} row {i}: missing tag_key")
            continue
        tags_by_col.setdefault(obj, {})[tkey] = tval
    tag_entries = [
        {"securable": {"type": "column", "name": col}, "tags": tg}
        for col, tg in sorted(tags_by_col.items())
    ]

    # ---- policies.yaml from the Masking Rules tab (column masks) ----------
    # when_key/when_value (table narrowing) are intentionally ignored for now: the mask is
    # scoped to `attach` and applies to columns tagged acm_pii_type=<value> under that scope.
    column_mask_policies: list[dict[str, Any]] = []
    for i, r in enumerate(wb.get(T_MASKING_RULES, [])):
        scope = r.get("attach")
        mck, mcv, fn = r.get("match_columns_key"), r.get("match_columns_value"), r.get("function")
        if mck != "acm_pii_type":
            problems.append(f"{T_MASKING_RULES} row {i}: match_columns_key {mck!r} must be 'acm_pii_type'")
        if not scope:
            problems.append(f"{T_MASKING_RULES} row {i}: missing attach (scope)")
        if not fn:
            problems.append(f"{T_MASKING_RULES} row {i}: missing function")
        if not (scope and fn and mck == "acm_pii_type"):
            continue
        column_mask_policies.append({
            "scope": scope,
            "column_matching_tags": {"acm_pii_type": mcv},
            "function": fn,
        })

    if problems:
        raise GeneratorError(
            "workbook cannot be converted to a valid config:\n  - " + "\n  - ".join(problems))

    policies_doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    if column_mask_policies:
        policies_doc["column_mask_policies"] = column_mask_policies

    docs: Docs = {
        "base_grants": {"schema_version": SCHEMA_VERSION, "grant_rules": grant_rules},
        "rbac_view_grants": {"schema_version": SCHEMA_VERSION, "grant_rules": rbac_rules},
        "rbac_views": {"schema_version": SCHEMA_VERSION, "views": views},  # expected dynamic views
        "labels": {"schema_version": SCHEMA_VERSION, "labels": labels_entries},
        "tags": {"schema_version": SCHEMA_VERSION, "tags": tag_entries},  # from Asset Classification
        "policies": policies_doc,                                        # column masks from Masking Rules
    }

    # Self-check: never emit YAML the framework's own validator would reject.
    result = validate_docs(docs)
    if not result.ok:
        raise GeneratorError("generated config failed validation:\n  "
                             + "\n  ".join(str(i) for i in result.sorted_issues()))
    return docs


# File name per docs short-key, in a stable write order.
_FILES = (("base_grants", "base_grants.yaml"), ("rbac_view_grants", "rbac_view_grants.yaml"),
          ("rbac_views", "rbac_views.yaml"), ("labels", "labels.yaml"),
          ("tags", "tags.yaml"), ("policies", "policies.yaml"))


def write_config(docs: Docs, out_dir: str | Path) -> Path:
    """Serialize the docs dict to ``<out_dir>/{grants,labels,tags,policies}.yaml``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for short, fname in _FILES:
        (out / fname).write_text(yaml.safe_dump(docs[short], sort_keys=False))
    return out
