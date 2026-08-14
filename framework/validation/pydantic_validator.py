"""Code-first Pydantic v2 validation for the Eastspring governance contract.

The contract is expressed as Pydantic models in the style of the
``20260522-bankx-ETL-redesign`` project: ``extra="forbid"`` (closed records),
``Literal`` enums, and ``@model_validator`` for the cross-field / cross-item
rules. YAML -> ``model_validate`` -> typed objects; ``validate_dir`` aggregates
the three files into a single ``ValidationResult`` of normalized ``Issue``s.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)


# ---- Normalized result types --------------------------------------------
#
# These decouple callers (CLI, CI, the future Compile step) from Pydantic's raw
# error-dict format and aggregate findings across the three config files.

@dataclass(frozen=True, order=True)
class Issue:
    """One validation finding.

    Ordered by (file, path) so results are stable and diffable regardless of the
    order Pydantic emits errors.
    """
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


# ---- Scalar/name types (anchored patterns) -------------------------------

_SEG = r"[^.]*[^.\s][^.]*"        # one dotted segment: no '.', >=1 non-space char

CatalogName = Annotated[str, StringConstraints(pattern=rf"^{_SEG}$")]
SchemaName = Annotated[str, StringConstraints(pattern=rf"^{_SEG}\.{_SEG}$")]
ObjectName = Annotated[str, StringConstraints(pattern=rf"^{_SEG}\.{_SEG}\.{_SEG}$")]
ColumnName = Annotated[str, StringConstraints(pattern=rf"^{_SEG}\.{_SEG}\.{_SEG}\.{_SEG}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
# FunctionName whose final segment starts with "acm_".
FrameworkManagedFunctionName = Annotated[
    str, StringConstraints(pattern=rf"^{_SEG}\.{_SEG}\.acm_[A-Za-z0-9_]*$")
]
# NonEmptyString: >=1 non-whitespace char (dots allowed).
NonEmptyString = Annotated[str, StringConstraints(pattern=r"^\s*\S.*$")]

SchemaVersion = Literal["1.0"]
ObjectType = Literal["catalog", "schema", "table", "view", "function", "volume"]
SecurableType = Literal["catalog", "schema", "table", "view", "function", "volume", "column"]
Bundle = Literal["READ", "WRITE", "OWNER", "ADMIN"]

AcmBusinessFunction = Literal["investments", "performance_attribution", "compliance", "quants"]
AcmPiiType = Literal["FULL_NAME", "identifier", "secret"]
AcmRlsDimension = Literal["region", "legal_entity"]

# Allowed grains for the BF asset tag (used for grant-match legality).
_BF_GRAINS = {"catalog", "schema", "table", "view"}

_NAME_PATTERN = {
    "catalog": CatalogName,
    "schema": SchemaName,
    "column": ColumnName,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---- Refs ----------------------------------------------------------------

def _check_name_shape(type_: str, name: str, allow_column: bool) -> None:
    import re
    if type_ == "catalog":
        pat = rf"^{_SEG}$"
    elif type_ == "schema":
        pat = rf"^{_SEG}\.{_SEG}$"
    elif type_ == "column":
        if not allow_column:
            raise ValueError("type 'column' is not a valid ObjectType")
        pat = rf"^{_SEG}\.{_SEG}\.{_SEG}\.{_SEG}$"
    else:  # table/view/function/volume
        pat = rf"^{_SEG}\.{_SEG}\.{_SEG}$"
    if not re.match(pat, name):
        raise ValueError(f"name {name!r} does not match the {type_} grain shape")


class ObjectRef(_Strict):
    type: ObjectType
    name: str

    @model_validator(mode="after")
    def _shape(self) -> "ObjectRef":
        _check_name_shape(self.type, self.name, allow_column=False)
        return self

    def key(self) -> str:
        return f"{self.type}:{self.name}"


class SecurableRef(_Strict):
    type: SecurableType
    name: str

    @model_validator(mode="after")
    def _shape(self) -> "SecurableRef":
        _check_name_shape(self.type, self.name, allow_column=True)
        return self

    def key(self) -> str:
        return f"{self.type}:{self.name}"


# ---- Tag maps ------------------------------------------------------------

class AssetTagMap(_Strict):
    """>=1 framework-managed asset-tag predicate. Only acm_business_function in v1."""
    acm_business_function: AcmBusinessFunction | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> "AssetTagMap":
        if self.acm_business_function is None:
            raise ValueError("asset tag map must declare at least one tag")
        return self


class FrameworkTagMap(_Strict):
    acm_business_function: AcmBusinessFunction | None = None
    acm_pii_type: AcmPiiType | None = None
    acm_rls: AcmRlsDimension | None = None

    @model_validator(mode="after")
    def _non_empty(self) -> "FrameworkTagMap":
        if self.acm_business_function is None and self.acm_pii_type is None and self.acm_rls is None:
            raise ValueError("tag map must declare at least one tag")
        return self


# ---- tags.yaml -----------------------------------------------------------

class TagAssignment(_Strict):
    securable: SecurableRef
    tags: FrameworkTagMap

    @model_validator(mode="after")
    def _grain_routing(self) -> "TagAssignment":
        is_col = self.securable.type == "column"
        if is_col and self.tags.acm_business_function is not None:
            raise ValueError("acm_business_function is an asset tag; it cannot apply to a column")
        if not is_col and (self.tags.acm_pii_type is not None or self.tags.acm_rls is not None):
            raise ValueError("acm_pii_type/acm_rls are column-only routing tags")
        return self


class TagsYaml(_Strict):
    schema_version: SchemaVersion
    tags: list[TagAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_securables(self) -> "TagsYaml":
        seen: dict[str, int] = {}
        for i, entry in enumerate(self.tags):
            k = entry.securable.key()
            if k in seen:
                raise ValueError(
                    f"securable {k} is repeated (first at index {seen[k]}); "
                    f"put all of its tags in one entry"
                )
            seen[k] = i
        return self


# ---- grants.yaml ---------------------------------------------------------

class GrantMatch(_Strict):
    object_types: list[ObjectType] = Field(min_length=1)
    tags: AssetTagMap

    @field_validator("object_types")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("object_types must be unique")
        return v

    @model_validator(mode="after")
    def _grain_legality(self) -> "GrantMatch":
        if self.tags.acm_business_function is not None:
            bad = [t for t in self.object_types if t not in _BF_GRAINS]
            if bad:
                raise ValueError(
                    f"acm_business_function does not permit object_type(s) {bad}; "
                    f"allowed grains: {sorted(_BF_GRAINS)}"
                )
        return self


class GrantRule(_Strict):
    groups: list[NonEmptyString] = Field(min_length=1)
    bundle: Bundle
    match: GrantMatch | None = None
    include: list[ObjectRef] | None = None
    exclude: list[ObjectRef] | None = None

    @field_validator("groups")
    @classmethod
    def _unique_groups(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("groups must be unique")
        return v

    @model_validator(mode="after")
    def _match_or_include(self) -> "GrantRule":
        if self.match is None and not self.include:
            raise ValueError("a rule must have `match` or a non-empty `include`")
        return self

    @model_validator(mode="after")
    def _include_exclude(self) -> "GrantRule":
        for field_name in ("include", "exclude"):
            refs = getattr(self, field_name) or []
            keys = [r.key() for r in refs]
            if len(set(keys)) != len(keys):
                raise ValueError(f"{field_name} contains duplicate objects")
        inc = {r.key() for r in (self.include or [])}
        exc = {r.key() for r in (self.exclude or [])}
        both = sorted(inc & exc)
        if both:
            raise ValueError(f"object(s) {both} appear in both include and exclude")
        return self


class GrantsYaml(_Strict):
    schema_version: SchemaVersion
    grant_rules: list[GrantRule] = Field(default_factory=list)


# ---- policies.yaml -------------------------------------------------------

def _validate_policy_scope(v: str) -> str:
    import re
    for pat in (rf"^{_SEG}$", rf"^{_SEG}\.{_SEG}$", rf"^{_SEG}\.{_SEG}\.{_SEG}$"):
        if re.match(pat, v):
            return v
    raise ValueError(f"scope {v!r} is not a catalog, schema, or table name")


class MaskColumnMatch(_Strict):
    acm_pii_type: AcmPiiType


class RlsCondition(_Strict):
    acm_rls: AcmRlsDimension


class RlsColumnMatch(_Strict):
    condition: RlsCondition
    as_: Identifier = Field(alias="as")


class ColumnMaskPolicy(_Strict):
    scope: str
    table_matching_tags: AssetTagMap | None = None
    column_matching_tags: MaskColumnMatch
    function: FrameworkManagedFunctionName

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        return _validate_policy_scope(v)


class RowFilterPolicy(_Strict):
    scope: str
    table_matching_tags: AssetTagMap | None = None
    column_matching_tags: list[RlsColumnMatch] = Field(min_length=1, max_length=3)
    function: FrameworkManagedFunctionName

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        return _validate_policy_scope(v)

    @model_validator(mode="after")
    def _unique_aliases(self) -> "RowFilterPolicy":
        aliases = [m.as_ for m in self.column_matching_tags]
        if len(set(aliases)) != len(aliases):
            dupes = sorted({a for a in aliases if aliases.count(a) > 1})
            raise ValueError(f"duplicate column alias(es) {dupes} within the policy")
        return self


class PoliciesYaml(_Strict):
    schema_version: SchemaVersion
    column_mask_policies: list[ColumnMaskPolicy] = Field(default_factory=list)
    row_filter_policies: list[RowFilterPolicy] = Field(default_factory=list)


# ---- Loader / validate_dir ----------------------------------------------

_FILE_MODEL = {
    "grants.yaml": GrantsYaml,
    "policies.yaml": PoliciesYaml,
    "tags.yaml": TagsYaml,
}


def validate_dir(config_dir: str | Path) -> ValidationResult:
    """Validate grants/policies/tags YAML in ``config_dir`` with the Pydantic models."""
    config_dir = Path(config_dir)
    result = ValidationResult(config_dir=str(config_dir))
    schema_versions: dict[str, Any] = {}

    for filename, model in _FILE_MODEL.items():
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

        try:
            model.model_validate(doc)
        except ValidationError as exc:
            for err in exc.errors():
                path_str = "/".join(str(p) for p in err["loc"])
                # value_error == a raised @model_validator/field ValueError -> semantic layer
                layer = "semantic" if err["type"] == "value_error" else "schema"
                result.issues.append(Issue(filename, path_str, err["msg"], layer))

    distinct = {v for v in schema_versions.values() if v is not None}
    if len(distinct) > 1:
        for filename, ver in schema_versions.items():
            result.issues.append(Issue(
                filename, "schema_version",
                f"schema_version {ver!r} disagrees with other files {sorted(distinct)}",
                "semantic",
            ))

    return result
