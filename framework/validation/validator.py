"""JSON Schema validation for the Eastspring governance contract.

The contract is authored as **YAML** JSON Schema files under
``schemas/config/v1/*.schema.yaml`` (Draft 2020-12 vocabulary, YAML syntax) and
enforced with the ``jsonschema`` library. Shared ``$defs`` live in
``common.schema.yaml`` and are resolved through a ``referencing`` registry.

JSON Schema is declarative, so it cannot express a handful of cross-field /
cross-item rules. Those run in a small structural ``semantic`` layer below and
need no external state. Deeper Static-Validate rules from the design doc
(mask/RLS coverage, function existence in ``functions.sql``, policy quotas) need
the compiled model / live state and belong to the Compile step, not here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


# ---- Normalized result types --------------------------------------------

@dataclass(frozen=True, order=True)
class Issue:
    """One validation finding, ordered by (file, path) for stable, diffable output.

    ``severity`` is "error" (fail-closed: schema/semantic violations that block) or "warning"
    (fail-open: e.g. references outside the reference allow-list). Only errors flip ``ok``."""
    file: str          # config file the issue is in, e.g. "grants.yaml"
    path: str          # location within the file, JSON-pointer style, e.g. "grant_rules/0/bundle"
    message: str = field(compare=False)
    layer: str = field(default="schema", compare=False)   # "schema" | "semantic" | "reference"
    severity: str = field(default="error", compare=False)  # "error" | "warning"

    def __str__(self) -> str:
        loc = f"{self.file}:{self.path}" if self.path else self.file
        return f"[{self.severity}/{self.layer}] {loc}: {self.message}"


@dataclass(frozen=True)
class Check:
    """One thing the validator inspected, recorded in execution order for verbose
    'what was checked' output. ``status`` is a display label:
    ok | issues | warnings | skipped | missing."""
    target: str        # file or check name, e.g. "base_grants.yaml", "schema_version", "reference"
    detail: str        # what was checked, e.g. "vs grants.schema.yaml; grant_rules=7"
    status: str = "ok"

    def __str__(self) -> str:
        return f"[{self.status}] {self.target}" + (f": {self.detail}" if self.detail else "")


@dataclass
class ValidationResult:
    """Outcome of validating a config directory. ``ok`` is False only if there are errors;
    warnings are reported but do not fail validation. ``checks`` records every inspection
    performed (for verbose output), independent of whether it found anything."""
    config_dir: str
    issues: list[Issue] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity != "warning"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def sorted_issues(self) -> list[Issue]:
        return sorted(self.issues)

    def summary(self) -> str:
        n_err, n_warn = len(self.errors), len(self.warnings)
        if not self.issues:
            return f"OK {self.config_dir} — no issues"
        head = "OK" if self.ok else "FAIL"
        return f"{head} {self.config_dir} — {n_err} error(s), {n_warn} warning(s)"


# ---- Paths / mappings ----------------------------------------------------

_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "config" / "v1"

_FILE_SCHEMA = {
    "base_grants.yaml": "grants.schema.yaml",   # base-table grant rules (was grants.yaml)
    "labels.yaml": "labels.schema.yaml",        # asset labels for grant matching (never materialized)
    "policies.yaml": "policies.schema.yaml",
    "tags.yaml": "tags.schema.yaml",
}

# Validated only if present (absence is not an error), unlike the required files above.
# The full-sync protected set + target catalog now live in the reference allow-list
# (config/reference/<deployment>.yaml), not in a governance.yaml.
_OPTIONAL_FILE_SCHEMA = {
    "rbac_view_grants.yaml": "grants.schema.yaml",   # serving-schema (view) grants; absent for base-only configs
    "rbac_views.yaml": "views.schema.yaml",          # expected dynamic views (for reconcile detection)
}

_ALL_FILE_SCHEMA = {**_FILE_SCHEMA, **_OPTIONAL_FILE_SCHEMA}


# ---- Structural semantic layer (rules JSON Schema cannot express) --------

def _check_grants(doc: Any) -> list[Issue]:
    """grants.yaml: no object may appear in both include and exclude of a rule."""
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return issues
    for i, rule in enumerate(_as_list(doc.get("grant_rules"))):
        if not isinstance(rule, dict):
            continue
        inc = {_ref_key(o) for o in _as_list(rule.get("include"))}
        exc = {_ref_key(o) for o in _as_list(rule.get("exclude"))}
        for key in sorted(k for k in inc & exc if k is not None):
            issues.append(Issue("grants.yaml", f"grant_rules/{i}",
                                f"object {key} appears in both include and exclude", "semantic"))
    return issues


def _check_tags(doc: Any) -> list[Issue]:
    """tags.yaml: a securable may appear only once (all its tags in one entry)."""
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return issues
    seen: dict[str, int] = {}
    for i, entry in enumerate(_as_list(doc.get("tags"))):
        if not isinstance(entry, dict):
            continue
        key = _ref_key(entry.get("securable"))
        if key is None:
            continue
        if key in seen:
            issues.append(Issue("tags.yaml", f"tags/{i}/securable",
                                f"securable {key} is repeated (first at tags/{seen[key]}); "
                                f"put all of its tags in one entry", "semantic"))
        else:
            seen[key] = i
    return issues


def _check_labels(doc: Any) -> list[Issue]:
    """labels.yaml: a securable may appear only once (all its labels in one entry)."""
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return issues
    seen: dict[str, int] = {}
    for i, entry in enumerate(_as_list(doc.get("labels"))):
        if not isinstance(entry, dict):
            continue
        key = _ref_key(entry.get("securable"))
        if key is None:
            continue
        if key in seen:
            issues.append(Issue("labels.yaml", f"labels/{i}/securable",
                                f"securable {key} is repeated (first at labels/{seen[key]}); "
                                f"put all of its labels in one entry", "semantic"))
        else:
            seen[key] = i
    return issues


def _check_views(doc: Any) -> list[Issue]:
    """views.yaml: a view fqn may be produced only once."""
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return issues
    seen: dict[str, int] = {}
    for i, entry in enumerate(_as_list(doc.get("views"))):
        if not isinstance(entry, dict):
            continue
        key = entry.get("view")
        if not isinstance(key, str):
            continue
        if key in seen:
            issues.append(Issue("rbac_views.yaml", f"views/{i}/view",
                                f"view {key} defined more than once (first at views/{seen[key]})", "semantic"))
        else:
            seen[key] = i
    return issues


_FILE_SEMANTIC = {
    "base_grants.yaml": _check_grants,
    "rbac_view_grants.yaml": _check_grants,
    "rbac_views.yaml": _check_views,
    "labels.yaml": _check_labels,
    "tags.yaml": _check_tags,
}


# ---- Schema loading + validation -----------------------------------------

def _build_registry(schema_dir: Path) -> Registry:
    """Load every *.schema.yaml into a referencing Registry keyed by $id."""
    registry = Registry()
    for path in sorted(schema_dir.glob("*.schema.yaml")):
        resource = Resource.from_contents(yaml.safe_load(path.read_text()))
        registry = resource @ registry
    return registry


def _path_str(error) -> str:
    return "/".join(str(p) for p in error.absolute_path)


def _validate_one(filename: str, doc: Any, schema_dir: Path, registry: Registry) -> list[Issue]:
    """Validate one parsed doc against its schema, then run its semantic checks."""
    issues: list[Issue] = []
    schema = yaml.safe_load((schema_dir / _ALL_FILE_SCHEMA[filename]).read_text())
    validator = Draft202012Validator(schema, registry=registry)
    # Order isn't set here: ValidationResult.sorted_issues() sorts everything by (file, path).
    issues.extend(
        Issue(filename, _path_str(err), err.message, "schema")
        for err in validator.iter_errors(doc)
    )
    issues.extend(_FILE_SEMANTIC.get(filename, lambda _doc: [])(doc))
    return issues


def _describe(doc: Any) -> str:
    """Compact 'key=count' of a doc's collections, for verbose output."""
    if not isinstance(doc, dict):
        return "non-mapping document"
    parts = [f"{k}={len(v)}" for k, v in doc.items() if isinstance(v, list)]
    return ", ".join(parts) if parts else "no collections"


def _describe_reference(reference: dict) -> str:
    """Compact summary of which dimensions the reference allow-list curates."""
    dims = [f"{key}={len(reference[key])}"
            for key in ("groups", "source_schemas", "governance_schemas", "serving_schemas", "data_products")
            if isinstance(reference.get(key), list)]
    gt = reference.get("governed_tags")
    if isinstance(gt, dict):
        dims.append(f"governed_tags={len(gt)} key(s)")
    return ", ".join(dims) if dims else "no curated dimensions"


def _record_file(result: "ValidationResult", filename: str, doc: Any,
                 schema_dir: Path, registry: Registry, schema_versions: dict[str, Any]) -> None:
    """Validate one loaded doc: track its schema_version, append its issues, and record a Check."""
    if isinstance(doc, dict):
        schema_versions[filename] = doc.get("schema_version")
    file_issues = _validate_one(filename, doc, schema_dir, registry)
    result.issues.extend(file_issues)
    result.checks.append(Check(filename, f"vs {_ALL_FILE_SCHEMA[filename]}; {_describe(doc)}",
                               "ok" if not file_issues else "issues"))


def _cross_file_version(schema_versions: dict[str, Any]) -> list[Issue]:
    """Every provided file must declare the same schema_version."""
    distinct = {v for v in schema_versions.values() if v is not None}
    if len(distinct) <= 1:
        return []
    return [
        Issue(filename, "schema_version",
              f"schema_version {ver!r} disagrees with other files {sorted(distinct)}",
              "semantic")
        for filename, ver in schema_versions.items()
    ]


# ---- reference allow-list layer (fail-open warnings) ---------------------
#
# The reference (``config/reference/<deployment>.yaml``) is the hand-curated, authoritative
# vocabulary a deployment may use: approved groups, catalog.schema pairs, and governed tag
# keys/values. It is NOT generated from the workbook — the workbook is validated *against* it.
# Anything the config uses that the reference does not list is a WARNING (severity="warning"),
# never an error: it flags drift for a human without blocking a deploy.

# Shape of a Microsoft Entra ID Object ID (8-4-4-4-12 hex) — a group's AIM external_id.
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def check_reference(docs: dict[str, Any], reference: Any) -> list[Issue]:
    """Warn (never fail) for groups / ``catalog.schema`` / tag keys+values the config uses
    but the ``reference`` allow-list does not list.

    ``docs`` may be keyed by short name (``"base_grants"``) or filename (``"base_grants.yaml"``).
    A reference dimension that is *absent* (key not present) is treated as "not curated" and
    skipped; a dimension present but empty means "nothing approved" and warns on everything used.
    """
    issues: list[Issue] = []
    if not isinstance(reference, dict):
        return issues
    by_file = {_SHORT_TO_FILE.get(key, key): doc for key, doc in docs.items()}

    # value -> (filename, path) of its first occurrence, so we warn once per distinct value.
    used_groups: dict[str, tuple[str, str]] = {}
    used_schemas: dict[str, tuple[str, str]] = {}
    used_tag_keys: dict[str, tuple[str, str]] = {}
    used_tag_pairs: dict[tuple[str, str], tuple[str, str]] = {}
    used_data_products: dict[str, tuple[str, str]] = {}
    used_view_targets: dict[str, tuple[str, str]] = {}

    def note(store: dict, key: Any, filename: str, path: str) -> None:
        store.setdefault(key, (filename, path))

    # groups + include/exclude schemas from the two grant files
    for filename in ("base_grants.yaml", "rbac_view_grants.yaml"):
        doc = by_file.get(filename)
        if not isinstance(doc, dict):
            continue
        for i, rule in enumerate(_as_list(doc.get("grant_rules"))):
            if not isinstance(rule, dict):
                continue
            for j, g in enumerate(_as_list(rule.get("groups"))):
                if isinstance(g, str):
                    note(used_groups, g, filename, f"grant_rules/{i}/groups/{j}")
            for key in ("include", "exclude"):
                for k, obj in enumerate(_as_list(rule.get(key))):
                    if isinstance(obj, dict) and (s := _schema_of(obj.get("name"))):
                        note(used_schemas, s, filename, f"grant_rules/{i}/{key}/{k}/name")
            match = rule.get("match")
            if isinstance(match, dict):
                dp = (match.get("labels") or {}).get("data_product")
                if isinstance(dp, str):
                    note(used_data_products, dp, filename, f"grant_rules/{i}/match/labels/data_product")

    # data_product asset labels from labels.yaml
    doc = by_file.get("labels.yaml")
    if isinstance(doc, dict):
        for i, entry in enumerate(_as_list(doc.get("labels"))):
            if not isinstance(entry, dict):
                continue
            dp = (entry.get("labels") or {}).get("data_product")
            if isinstance(dp, str):
                note(used_data_products, dp, "labels.yaml", f"labels/{i}/labels/data_product")

    # rbac_views: the view's DESTINATION schema must be a serving schema (role check); the base
    # SOURCE schema is a normal approved-schema reference.
    doc = by_file.get("rbac_views.yaml")
    if isinstance(doc, dict):
        for i, v in enumerate(_as_list(doc.get("views"))):
            if not isinstance(v, dict):
                continue
            if s := _schema_of(v.get("view")):
                note(used_view_targets, s, "rbac_views.yaml", f"views/{i}/view")
            if s := _schema_of(v.get("source")):
                note(used_schemas, s, "rbac_views.yaml", f"views/{i}/source")

    # schemas + tag keys/values from tags
    doc = by_file.get("tags.yaml")
    if isinstance(doc, dict):
        for i, entry in enumerate(_as_list(doc.get("tags"))):
            if not isinstance(entry, dict):
                continue
            if s := _schema_of((entry.get("securable") or {}).get("name")):
                note(used_schemas, s, "tags.yaml", f"tags/{i}/securable/name")
            tagmap = entry.get("tags")
            if isinstance(tagmap, dict):
                for tk, tv in tagmap.items():
                    note(used_tag_keys, tk, "tags.yaml", f"tags/{i}/tags/{tk}")
                    if isinstance(tv, str):
                        note(used_tag_pairs, (tk, tv), "tags.yaml", f"tags/{i}/tags/{tk}")

    # schemas from policy scope + function FQNs; tag key/value from mask routing tags
    doc = by_file.get("policies.yaml")
    if isinstance(doc, dict):
        for i, pol in enumerate(_as_list(doc.get("column_mask_policies"))):
            if not isinstance(pol, dict):
                continue
            for fld in ("scope", "function"):
                if s := _schema_of(pol.get(fld)):
                    note(used_schemas, s, "policies.yaml", f"column_mask_policies/{i}/{fld}")
            # the mask's routing tag (acm_pii_type: <value>) is governed vocabulary too
            cmt = pol.get("column_matching_tags")
            if isinstance(cmt, dict):
                for tk, tv in cmt.items():
                    path = f"column_mask_policies/{i}/column_matching_tags/{tk}"
                    note(used_tag_keys, tk, "policies.yaml", path)
                    if isinstance(tv, str):
                        note(used_tag_pairs, (tk, tv), "policies.yaml", path)

    _REF = "reference allow-list (config/reference)"
    # groups is a list of {name, external_id: <Entra Object ID | null>}; the names are the approved
    # set. external_id is only shape-checked here — whether it is *required* depends on the
    # provisioning mode (AIM vs local), enforced by the provisioning step.
    allowed_groups = reference.get("groups")
    if allowed_groups is not None:
        entries = [g if isinstance(g, dict) else {} for g in allowed_groups]
        allowed = {g.get("name") for g in entries if g.get("name")}
        for g, (fn, path) in sorted(used_groups.items()):
            if g not in allowed:
                issues.append(Issue(fn, path, f"group {g!r} is not in the {_REF}",
                                    "reference", "warning"))
        seen: set[str] = set()
        for i, entry in enumerate(entries):
            name, ext = entry.get("name"), entry.get("external_id")
            if not name:
                issues.append(Issue("reference", f"groups/{i}", "group entry has no name",
                                    "reference", "warning"))
            elif name in seen:
                issues.append(Issue("reference", f"groups/{i}/name",
                                    f"group {name!r} is listed more than once",
                                    "reference", "warning"))
            seen.add(name)
            if ext is not None and not _GUID.fullmatch(str(ext)):
                issues.append(Issue("reference", f"groups/{i}/external_id",
                                    f"external_id {ext!r} for group {name!r} is not a GUID "
                                    "(expected the Entra ID Object ID)",
                                    "reference", "warning"))

    # approved-schema set = union of the role buckets (source + governance + serving).
    src_schemas = reference.get("source_schemas")
    srv_schemas = reference.get("serving_schemas")
    gov_schemas = reference.get("governance_schemas")
    if src_schemas is not None or srv_schemas is not None or gov_schemas is not None:
        allowed = set(src_schemas or []) | set(srv_schemas or []) | set(gov_schemas or [])
        for s, (fn, path) in sorted(used_schemas.items()):
            if s not in allowed:
                issues.append(Issue(fn, path, f"schema {s!r} is not in the {_REF}",
                                    "reference", "warning"))

    # a dynamic view's target (destination) schema must be a declared serving_schema.
    if srv_schemas is not None:
        serving = set(srv_schemas)
        for s, (fn, path) in sorted(used_view_targets.items()):
            if s not in serving:
                issues.append(Issue(fn, path,
                                    f"view target schema {s!r} is not a declared serving_schema",
                                    "reference", "warning"))

    allowed_data_products = reference.get("data_products")
    if allowed_data_products is not None:
        allowed = set(allowed_data_products)
        for dp, (fn, path) in sorted(used_data_products.items()):
            if dp not in allowed:
                issues.append(Issue(fn, path, f"data_product {dp!r} is not in the {_REF}",
                                    "reference", "warning"))

    # governed_tags maps each approved acm_ tag key -> its approved values. Its keys are the
    # approved tag-key set; its per-key list is the approved value set. Absent => tag vocabulary
    # is not curated (skipped). A key whose value list is null is allowed but not value-checked.
    governed = reference.get("governed_tags")
    if isinstance(governed, dict):
        allowed_keys = set(governed)
        for tk, (fn, path) in sorted(used_tag_keys.items()):
            if tk not in allowed_keys:
                issues.append(Issue(fn, path, f"tag key {tk!r} is not in the {_REF}",
                                    "reference", "warning"))
        for (tk, tv), (fn, path) in sorted(used_tag_pairs.items()):
            allowed_vals = governed.get(tk)
            # only value-check keys the reference curates; unknown keys already warned above.
            if allowed_vals is not None and tv not in set(allowed_vals):
                issues.append(Issue(fn, path,
                                    f"tag value {tv!r} for key {tk!r} is not in the {_REF}",
                                    "reference", "warning"))

    return issues


def _load_reference(config_dir: Path) -> dict[str, Any] | None:
    """Auto-discover the reference allow-list at ``config/reference/<config_dir.name>.yaml``.
    Returns None if absent or unparseable (a missing reference simply skips the warning layer)."""
    ref_path = config_dir.parent / "reference" / f"{config_dir.name}.yaml"
    if not ref_path.exists():
        return None
    try:
        loaded = yaml.safe_load(ref_path.read_text())
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None


def validate_dir(config_dir: str | Path, schema_dir: str | Path | None = None,
                 reference: Any = None) -> ValidationResult:
    """Validate grants/policies/tags YAML in ``config_dir`` against the YAML schemas.

    If ``reference`` is None, the allow-list at ``config/reference/<config_dir.name>.yaml`` is
    auto-loaded (when present) and its warnings appended. Pass an explicit dict to override, or
    ``{}`` to disable the warning layer.
    """
    config_dir = Path(config_dir)
    schema_dir = Path(schema_dir) if schema_dir is not None else _DEFAULT_SCHEMA_DIR
    result = ValidationResult(config_dir=str(config_dir))

    registry = _build_registry(schema_dir)
    schema_versions: dict[str, Any] = {}
    parsed: dict[str, Any] = {}

    for filename in _FILE_SCHEMA:
        path = config_dir / filename
        if not path.exists():
            result.issues.append(Issue(filename, "", "file not found", "schema"))
            result.checks.append(Check(filename, "required file not found", "missing"))
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            result.issues.append(Issue(filename, "", f"YAML parse error: {exc}", "schema"))
            result.checks.append(Check(filename, "YAML parse", "issues"))
            continue
        parsed[filename] = doc
        _record_file(result, filename, doc, schema_dir, registry, schema_versions)

    for filename in _OPTIONAL_FILE_SCHEMA:
        path = config_dir / filename
        if not path.exists():
            result.checks.append(Check(filename, "optional file, not present", "skipped"))
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            result.issues.append(Issue(filename, "", f"YAML parse error: {exc}", "schema"))
            result.checks.append(Check(filename, "YAML parse", "issues"))
            continue
        parsed[filename] = doc
        _record_file(result, filename, doc, schema_dir, registry, schema_versions)

    version_issues = _cross_file_version(schema_versions)
    result.issues.extend(version_issues)
    result.checks.append(Check("schema_version", f"agreement across {len(schema_versions)} file(s)",
                               "ok" if not version_issues else "issues"))

    ref_path = config_dir.parent / "reference" / f"{config_dir.name}.yaml"
    if reference is None:
        reference = _load_reference(config_dir)
    if isinstance(reference, dict):
        ref_issues = check_reference(parsed, reference)
        result.issues.extend(ref_issues)
        result.checks.append(Check("reference", _describe_reference(reference),
                                   "warnings" if ref_issues else "ok"))
    else:
        result.checks.append(Check("reference", f"no allow-list found ({ref_path.name})", "skipped"))

    return result


# Short keys the Databricks Job passes as JSON dict params.
_SHORT_TO_FILE = {"base_grants": "base_grants.yaml", "rbac_view_grants": "rbac_view_grants.yaml",
                  "rbac_views": "rbac_views.yaml", "policies": "policies.yaml", "tags": "tags.yaml",
                  "labels": "labels.yaml"}


def validate_docs(docs: dict[str, Any], schema_dir: str | Path | None = None,
                  reference: Any = None) -> ValidationResult:
    """Validate already-parsed config dicts against the YAML schemas.

    ``docs`` may be keyed by short name (``"grants"``) or filename (``"grants.yaml"``).
    Use this when config arrives as in-memory dicts (e.g. JSON job params) rather than
    files on disk. A missing dimension is reported as an issue, mirroring ``validate_dir``.
    Pass ``reference`` (the allow-list dict) to also append fail-open reference warnings; when
    None the warning layer is skipped (there is no directory to auto-discover it from).
    """
    schema_dir = Path(schema_dir) if schema_dir is not None else _DEFAULT_SCHEMA_DIR
    by_file = {_SHORT_TO_FILE.get(key, key): doc for key, doc in docs.items()}

    result = ValidationResult(config_dir="<docs>")
    registry = _build_registry(schema_dir)
    schema_versions: dict[str, Any] = {}

    for filename in _FILE_SCHEMA:
        doc = by_file.get(filename)
        if doc is None:
            result.issues.append(Issue(filename, "", "document not provided", "schema"))
            result.checks.append(Check(filename, "required document not provided", "missing"))
            continue
        _record_file(result, filename, doc, schema_dir, registry, schema_versions)

    for filename in _OPTIONAL_FILE_SCHEMA:
        doc = by_file.get(filename)
        if doc is None:
            result.checks.append(Check(filename, "optional document, not provided", "skipped"))
            continue
        _record_file(result, filename, doc, schema_dir, registry, schema_versions)

    version_issues = _cross_file_version(schema_versions)
    result.issues.extend(version_issues)
    result.checks.append(Check("schema_version", f"agreement across {len(schema_versions)} file(s)",
                               "ok" if not version_issues else "issues"))

    if isinstance(reference, dict):
        ref_issues = check_reference(docs, reference)
        result.issues.extend(ref_issues)
        result.checks.append(Check("reference", _describe_reference(reference),
                                   "warnings" if ref_issues else "ok"))
    else:
        result.checks.append(Check("reference", "no allow-list provided", "skipped"))
    return result


# ---- helpers -------------------------------------------------------------

def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _ref_key(ref: Any) -> str | None:
    """Stable identity for an ObjectRef/SecurableRef: 'type:name'."""
    if isinstance(ref, dict) and "type" in ref and "name" in ref:
        return f"{ref['type']}:{ref['name']}"
    return None


def _schema_of(fqn: Any) -> str | None:
    """``catalog.schema`` from a securable FQN (catalog-only refs yield None)."""
    if not isinstance(fqn, str):
        return None
    parts = fqn.split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else None
