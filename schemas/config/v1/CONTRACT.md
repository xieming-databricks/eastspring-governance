# YAML Configuration Contract — v1.0

Extracted from *[Eastspring] Governance Framework: Technical Design* (v.1, 2026-07-30).

## File contract

The compiler loads these files into one model per environment directory (e.g. `config/uat/`).
A securable may appear only once in `tags.yaml` (and only once in `labels.yaml`); all of its
tags (or labels) belong in that one entry. Repeating a securable or placing a block in the
wrong file is a validation failure.

```
config/<env>/
  base_grants.yaml  # Platform/Security: base-table Grant Rules (select objects by label / include)
  rbac_grants.yaml  # Platform/Security: serving-schema (view) grants — optional; view-based configs
  labels.yaml       # Data Owners: asset labels for grant selection — NOT materialized to UC
  tags.yaml         # Data Owners: governed column-tag assignments (materialized for ABAC)
  policies.yaml     # Platform/Security: ABAC policy bindings
  functions.sql     # Platform/DE/Security: executable entitlement behavior (SQL, not YAML)
```

`base_grants.yaml` and `rbac_grants.yaml` share the same schema (`GrantsYaml`); the compiler
merges both into one grant set. `rbac_grants.yaml` is optional (absent for base-only configs).

## Schema versioning

- The three generated YAML files conform to versioned [JSON Schema 2020-12] schemas
  delivered with the framework under `schemas/config/v1/`.
- Each parsed YAML file is validated before compilation using Python's `jsonschema` library.
- Each file carries the same `schema_version`; unknown fields and unsupported versions fail
  validation.
- Schema versions follow Semantic Versioning 2.0.0.

Schemas are authored in YAML (Draft 2020-12 vocabulary, YAML syntax). Shared
definitions are held in `common.schema.yaml`. File-to-schema mapping:

| Generated file      | JSON Schema (YAML)     | Root type     |
| :------------------ | :-------------------- | :------------ |
| `base_grants.yaml`   | `grants.schema.yaml`   | `GrantsYaml`   |
| `rbac_grants.yaml`   | `grants.schema.yaml`   | `GrantsYaml`   |
| `labels.yaml`        | `labels.schema.yaml`   | `LabelsYaml`   |
| `policies.yaml`      | `policies.schema.yaml` | `PoliciesYaml` |
| `tags.yaml`          | `tags.schema.yaml`     | `TagsYaml`     |

## Type model (concrete v1.0 structure)

`?` means optional, `[n..m]` gives collection cardinality, and `UniqueList` rejects
duplicate values. All records are closed: fields not shown are invalid.

```
SchemaVersion = "1.0"
NonEmptyString = string containing at least one non-whitespace character
NamePart = NonEmptyString containing no "."
Identifier = string matching [A-Za-z_][A-Za-z0-9_]*

CatalogName = NamePart
SchemaName = NamePart "." NamePart
ObjectName = NamePart "." NamePart "." NamePart
ColumnName = NamePart "." NamePart "." NamePart "." NamePart
FunctionName = ObjectName
FrameworkManagedFunctionName = FunctionName whose final name starts with "acm_"
FrameworkGeneratedPolicyName = Identifier starting with "acm_policy_"
PolicyScope = CatalogName | SchemaName | ObjectName backed by a table
ObjectRef = {type: ObjectType, name: qualified name for type}
SecurableRef = {type: SecurableType, name: qualified name for type}


ObjectType = "catalog" | "schema" | "table" | "view" | "function" | "volume"
SecurableType = ObjectType | "column"
Bundle = "READ" | "WRITE" | "OWNER" | "ADMIN"

FrameworkManagedTagKey = closed enum of MATERIALIZED tag keys (acm_pii_type, acm_rls); every member starts with "acm_"
AllowedTagValues<K> = closed key-dependent string enum for a tag/label key K
BusinessFunction = business-function vocabulary (investments | performance_attribution | compliance | quants)
LabelMap = {business_function?: NonEmptyString, data_product?: NonEmptyString}[1..*]   # config-only asset labels; values are free strings; NEVER materialized to UC
AssetTagMap = {acm_business_function: BusinessFunction}     # ABAC policy table-matching only (policies.yaml)
AcmPiiType = AllowedTagValues<"acm_pii_type">
AcmRlsDimension = AllowedTagValues<"acm_rls">

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
  tags: {acm_pii_type?: AcmPiiType, acm_rls?: AcmRlsDimension}[1..*]
}

ColumnMaskPolicy = {
  scope: PolicyScope,
  table_matching_tags?: AssetTagMap,
  column_matching_tags: {acm_pii_type: AcmPiiType},
  function: FrameworkManagedFunctionName
}
RlsColumnMatch = {
  condition: {acm_rls: AcmRlsDimension},
  as: Identifier
}
RowFilterPolicy = {
  scope: PolicyScope,
  table_matching_tags?: AssetTagMap,
  column_matching_tags: List<RlsColumnMatch>[1..3],
  function: FrameworkManagedFunctionName
} where column aliases are unique within the policy

GrantsYaml = {
  schema_version: SchemaVersion,
  grant_rules: List<GrantRule>[0..*]
}
PoliciesYaml = {
  schema_version: SchemaVersion,
  column_mask_policies: List<ColumnMaskPolicy>[0..*],
  row_filter_policies: List<RowFilterPolicy>[0..*]
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
  `tags.yaml` holds the column-routing *tags* (`acm_pii_type`, `acm_rls`) that **are**
  materialized to UC for ABAC masking / RLS. `acm_business_function` survives only as the
  ABAC policy table-matching key (`AssetTagMap`, still a closed enum); policies are
  detect-only in v1.
- For `ObjectRef` and `SecurableRef`, catalogs use `CatalogName`, schemas use `SchemaName`,
  columns use `ColumnName`, and all other types use `ObjectName`.
- Tags outside the `acm_` namespace are not selected, assigned, or removed by this framework.

[JSON Schema 2020-12]: https://json-schema.org/draft/2020-12
