# YAML Configuration Contract — v1.0

Extracted from *[Eastspring] Governance Framework: Technical Design* (v.1, 2026-07-30).

## File contract

The compiler loads these files into one model per environment directory (e.g. `config/uat/`).
A securable may appear only once in `tags.yaml` (and only once in `labels.yaml`); all of its
tags (or labels) belong in that one entry. Repeating a securable or placing a block in the
wrong file is a validation failure.

```
config/<env>/
  base_grants.yaml       # Platform/Security: base-table Grant Rules (select objects by label / include)
  rbac_view_grants.yaml  # Platform/Security: serving-schema (view) grants — optional; view-based configs
  rbac_views.yaml        # expected dynamic views (base table -> serving schema) — optional; for reconcile detection
  labels.yaml            # Data Owners: asset labels for grant selection — NOT materialized to UC
  tags.yaml              # Data Owners: governed column-tag assignments (materialized for ABAC)
  policies.yaml          # Platform/Security: ABAC policy bindings
  functions.sql          # Platform/DE/Security: executable entitlement behavior (SQL, not YAML)
```

`base_grants.yaml` and `rbac_view_grants.yaml` share the same schema (`GrantsYaml`); the compiler
merges both into one grant set. `rbac_view_grants.yaml` and `rbac_views.yaml` are optional (absent
for base-only configs).

## Schema versioning

- The three generated YAML files conform to versioned [JSON Schema 2020-12] schemas
  delivered inside the `eastspring-uc-governance` package under
  `eastspring_uc_governance/schemas/config/v1/` (shipped in the wheel).
- Each parsed YAML file is validated before compilation using Python's `jsonschema` library.
- Each file carries the same `schema_version`; unknown fields and unsupported versions fail
  validation.
- Schema versions follow Semantic Versioning 2.0.0.

Schemas are authored in YAML (Draft 2020-12 vocabulary, YAML syntax). Shared
definitions are held in `common.schema.yaml`. File-to-schema mapping:

| Generated file      | JSON Schema (YAML)     | Root type     |
| :------------------ | :-------------------- | :------------ |
| `base_grants.yaml`      | `grants.schema.yaml`   | `GrantsYaml`   |
| `rbac_view_grants.yaml` | `grants.schema.yaml`   | `GrantsYaml`   |
| `rbac_views.yaml`       | `views.schema.yaml`    | `ViewsYaml`    |
| `labels.yaml`           | `labels.schema.yaml`   | `LabelsYaml`   |
| `policies.yaml`         | `policies.schema.yaml` | `PoliciesYaml` |
| `tags.yaml`             | `tags.schema.yaml`     | `TagsYaml`     |

## Type model (concrete v1.0 structure)

`?` means optional, `[n..m]` gives collection cardinality, and `UniqueList` rejects
duplicate values. All records are closed: fields not shown are invalid.

```
SchemaVersion = "1.0"
NonEmptyString = string containing at least one non-whitespace character
NamePart = NonEmptyString containing no "."

CatalogName = NamePart
SchemaName = NamePart "." NamePart
ObjectName = NamePart "." NamePart "." NamePart
ColumnName = NamePart "." NamePart "." NamePart "." NamePart
FunctionName = ObjectName
FrameworkManagedFunctionName = FunctionName whose final name starts with "acm_"
FrameworkGeneratedPolicyName = compiler-generated name matching acm_policy_[A-Za-z0-9_]*
PolicyScope = CatalogName | SchemaName | ObjectName backed by a table
ObjectRef = {type: ObjectType, name: qualified name for type}
SecurableRef = {type: SecurableType, name: qualified name for type}


ObjectType = "catalog" | "schema" | "table" | "view" | "function" | "volume"
SecurableType = ObjectType | "column"
Bundle = "READ" | "WRITE" | "OWNER" | "ADMIN"

FrameworkManagedTagKey = closed enum of MATERIALIZED tag keys (acm_pii_type); every member starts with "acm_"
LabelMap = {business_function?: NonEmptyString, data_product?: NonEmptyString}[1..*]   # config-only asset labels; values are free strings; NEVER materialized to UC

GrantMatch = {
  object_types: UniqueList<ObjectType>[1..*],
  labels: LabelMap
} where business_function permits only catalog/schema/table/view grains

ExceptionRef = ObjectRef + {exp_id?: NonEmptyString}   # include/exclude entry; exp_id traces to an Access-Matrix exception (e.g. EX001)
GrantRule = {
  rule_id?: NonEmptyString,                            # source Access-Matrix Grant Rule id (e.g. GR001), for traceability
  groups: UniqueList<NonEmptyString>[1..*],
  bundle: Bundle,
  match?: GrantMatch,
  include?: UniqueList<ExceptionRef>[0..*],
  exclude?: UniqueList<ExceptionRef>[0..*]
} where match is present or include contains at least one object

LabelAssignment = {
  securable: ObjectRef,                             # asset securables only (never column)
  labels: LabelMap
}
TagAssignment = {
  securable: {type: "column", name: ColumnName},    # column securables only
  tags: {acm_pii_type: NonEmptyString}[1..*]   # value vocabulary validated (fail-open) against the reference allow-list, not a closed enum
}

ColumnMaskPolicy = {
  scope: PolicyScope,                                   # applies to every table in scope (no table narrowing)
  column_matching_tags: {acm_pii_type: NonEmptyString},
  function: FrameworkManagedFunctionName
}

GrantsYaml = {
  schema_version: SchemaVersion,
  grant_rules: List<GrantRule>[0..*]
}
PoliciesYaml = {
  schema_version: SchemaVersion,
  column_mask_policies: List<ColumnMaskPolicy>[0..*]
}
LabelsYaml = {
  schema_version: SchemaVersion,
  labels: List<LabelAssignment>[0..*]     # required file; may be empty for include-only configs
}
TagsYaml = {
  schema_version: SchemaVersion,
  tags: List<TagAssignment>[0..*]
}
```

## Notes

- The framework-managed key vocabulary, each key's allowed values, and each key's allowed
  securable grains are closed JSON Schema `enum` definitions, not free-form strings. The
  schemas enumerate the approved vocabulary for the release.
- **Labels vs tags.** `labels.yaml` holds asset *labels* (`business_function` and/or
  `data_product`) used only to *select* securables for grant rules; label values are
  free-form non-empty strings (the approved set is enforced at generation time, not by a
  closed enum). Labels are config-only and are **never written to Unity Catalog**.
  `tags.yaml` holds the column-routing *tags* (`acm_pii_type`) that **are** materialized to
  UC for ABAC masking. A column-mask policy has no table-matching condition (the workbook's
  Masking Rules `when_key`/`when_value` are not supported — a filled one fails generation): it
  masks every column tagged `acm_pii_type=<value>` under its scope.
  Row-level security is view-based (dynamic-view `rls_column`), not a materialized tag /
  row-filter policy.
- For `ObjectRef` and `SecurableRef`, catalogs use `CatalogName`, schemas use `SchemaName`,
  columns use `ColumnName`, and all other types use `ObjectName`.
- Tags outside the `acm_` namespace are not selected, assigned, or removed by this framework.

[JSON Schema 2020-12]: https://json-schema.org/draft/2020-12
