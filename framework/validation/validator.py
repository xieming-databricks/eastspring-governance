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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


# ---- Normalized result types --------------------------------------------

@dataclass(frozen=True, order=True)
class Issue:
    """One validation finding, ordered by (file, path) for stable, diffable output."""
    file: str          # config file the issue is in, e.g. "grants.yaml"
    path: str          # location within the file, JSON-pointer style, e.g. "grant_rules/0/bundle"
    message: str = field(compare=False)
    layer: str = field(default="schema", compare=False)   # "schema" | "semantic"

    def __str__(self) -> str:
        loc = f"{self.file}:{self.path}" if self.path else self.file
        return f"[{self.layer}] {loc}: {self.message}"


@dataclass
class ValidationResult:
    """Outcome of validating a config directory."""
    config_dir: str
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def sorted_issues(self) -> list[Issue]:
        return sorted(self.issues)

    def summary(self) -> str:
        if self.ok:
            return f"OK {self.config_dir} — no issues"
        n = len(self.issues)
        schema_n = sum(1 for i in self.issues if i.layer == "schema")
        sem_n = n - schema_n
        return (
            f"FAIL {self.config_dir} — {n} issue(s) "
            f"({schema_n} schema, {sem_n} semantic)"
        )


# ---- Paths / mappings ----------------------------------------------------

_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "config" / "v1"

_FILE_SCHEMA = {
    "base_grants.yaml": "grants.schema.yaml",   # base-table grant rules (was grants.yaml)
    "labels.yaml": "labels.schema.yaml",        # asset labels for grant matching (never materialized)
    "policies.yaml": "policies.schema.yaml",
    "tags.yaml": "tags.schema.yaml",
}

# Validated only if present (absence is not an error), unlike the required files above.
_OPTIONAL_FILE_SCHEMA = {
    "rbac_view_grants.yaml": "grants.schema.yaml",   # serving-schema (view) grants; absent for base-only configs
    "rbac_views.yaml": "views.schema.yaml",          # expected dynamic views (for reconcile detection)
    "governance.yaml": "governance.schema.yaml",
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


def _check_policies(doc: Any) -> list[Issue]:
    """policies.yaml: row-filter column aliases must be unique within a policy."""
    issues: list[Issue] = []
    if not isinstance(doc, dict):
        return issues
    for i, pol in enumerate(_as_list(doc.get("row_filter_policies"))):
        if not isinstance(pol, dict):
            continue
        aliases = [m["as"] for m in _as_list(pol.get("column_matching_tags"))
                   if isinstance(m, dict) and isinstance(m.get("as"), str)]
        for a in sorted({a for a in aliases if aliases.count(a) > 1}):
            issues.append(Issue("policies.yaml", f"row_filter_policies/{i}",
                                f"duplicate column alias {a!r} within the policy", "semantic"))
    return issues


_FILE_SEMANTIC = {
    "base_grants.yaml": _check_grants,
    "rbac_view_grants.yaml": _check_grants,
    "rbac_views.yaml": _check_views,
    "labels.yaml": _check_labels,
    "policies.yaml": _check_policies,
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


def validate_dir(config_dir: str | Path, schema_dir: str | Path | None = None) -> ValidationResult:
    """Validate grants/policies/tags YAML in ``config_dir`` against the YAML schemas."""
    config_dir = Path(config_dir)
    schema_dir = Path(schema_dir) if schema_dir is not None else _DEFAULT_SCHEMA_DIR
    result = ValidationResult(config_dir=str(config_dir))

    registry = _build_registry(schema_dir)
    schema_versions: dict[str, Any] = {}

    for filename in _FILE_SCHEMA:
        path = config_dir / filename
        if not path.exists():
            result.issues.append(Issue(filename, "", "file not found", "schema"))
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            result.issues.append(Issue(filename, "", f"YAML parse error: {exc}", "schema"))
            continue
        if isinstance(doc, dict):
            schema_versions[filename] = doc.get("schema_version")
        result.issues.extend(_validate_one(filename, doc, schema_dir, registry))

    for filename in _OPTIONAL_FILE_SCHEMA:
        path = config_dir / filename
        if not path.exists():
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            result.issues.append(Issue(filename, "", f"YAML parse error: {exc}", "schema"))
            continue
        if isinstance(doc, dict):
            schema_versions[filename] = doc.get("schema_version")
        result.issues.extend(_validate_one(filename, doc, schema_dir, registry))

    result.issues.extend(_cross_file_version(schema_versions))
    return result


# Short keys the Databricks Job passes as JSON dict params.
_SHORT_TO_FILE = {"base_grants": "base_grants.yaml", "rbac_view_grants": "rbac_view_grants.yaml",
                  "rbac_views": "rbac_views.yaml", "policies": "policies.yaml", "tags": "tags.yaml",
                  "labels": "labels.yaml", "governance": "governance.yaml"}


def validate_docs(docs: dict[str, Any], schema_dir: str | Path | None = None) -> ValidationResult:
    """Validate already-parsed config dicts against the YAML schemas.

    ``docs`` may be keyed by short name (``"grants"``) or filename (``"grants.yaml"``).
    Use this when config arrives as in-memory dicts (e.g. JSON job params) rather than
    files on disk. A missing dimension is reported as an issue, mirroring ``validate_dir``.
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
            continue
        if isinstance(doc, dict):
            schema_versions[filename] = doc.get("schema_version")
        result.issues.extend(_validate_one(filename, doc, schema_dir, registry))

    for filename in _OPTIONAL_FILE_SCHEMA:
        doc = by_file.get(filename)
        if doc is None:
            continue
        if isinstance(doc, dict):
            schema_versions[filename] = doc.get("schema_version")
        result.issues.extend(_validate_one(filename, doc, schema_dir, registry))

    result.issues.extend(_cross_file_version(schema_versions))
    return result


# ---- helpers -------------------------------------------------------------

def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _ref_key(ref: Any) -> str | None:
    """Stable identity for an ObjectRef/SecurableRef: 'type:name'."""
    if isinstance(ref, dict) and "type" in ref and "name" in ref:
        return f"{ref['type']}:{ref['name']}"
    return None
