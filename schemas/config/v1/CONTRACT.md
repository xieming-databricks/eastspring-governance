# YAML Configuration Contract — v1.0

Extracted from *[Eastspring] Governance Framework: Technical Design* (v.1, 2026-07-30).

## File contract

The compiler loads exactly these four files into one model per environment directory
(e.g. `config/uat/`). A securable may appear only once in `tags.yaml`; all of its tags
belong in that one entry. Repeating a securable or placing a block in the wrong file is a
validation failure.

```
config/<env>/
  grants.yaml       # Platform/Security: Grant Rules
  tags.yaml         # Data Owners: governed-tag assignments
  policies.yaml     # Platform/Security: ABAC policy bindings
  functions.sql     # Platform/DE/Security: executable entitlement behavior (SQL, not YAML)
```

## Schema versioning

- The three generated YAML files conform to versioned [JSON Schema 2020-12] schemas
  delivered with the framework under `schemas/config/v1/`.
- Each parsed YAML file is validated before compilation using Python's `jsonschema` library.
- Each file carries the same `schema_version`; unknown fields and unsupported versions fail
  validation.
- Schema versions follow Semantic Versioning 2.0.0.

Shared definitions are held in `common.schema.json`. File-to-schema mapping:

| Generated file | JSON Schema           | Root type     |
| :------------- | :-------------------- | :------------ |
| `grants.yaml`   | `grants.schema.json`   | `GrantsYaml`   |
| `policies.yaml` | `policies.schema.json` | `PoliciesYaml` |
| `tags.yaml`     | `tags.schema.json`     | `TagsYaml`     |

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

FrameworkManagedTagKey = closed string enum in common.schema.json; every member starts with "acm_"
AllowedTagValues<K> = closed key-dependent string enum for FrameworkManagedTagKey K
AllowedTagGrains<K> = closed key-dependent subset of SecurableType for FrameworkManagedTagKey K
FrameworkTagMap<G> = map<K, AllowedTagValues<K>>[1..*]
  where K is a FrameworkManagedTagKey and G is in AllowedTagGrains<K>
AssetTagMap = map<K, AllowedTagValues<K>>[1..*]
  where K is a FrameworkManagedTagKey and AllowedTagGrains<K> contains an ObjectType member
AcmBusinessFunction = AllowedTagValues<"acm_business_function">
AcmPiiType = AllowedTagValues<"acm_pii_type">
AcmRlsDimension = AllowedTagValues<"acm_rls">

GrantMatch = {
  object_types: UniqueList<ObjectType>[1..*],
  tags: AssetTagMap
} where every tag key permits every selected object_type grain

GrantRule = {
  groups: UniqueList<NonEmptyString>[1..*],
  bundle: Bundle,
  match?: GrantMatch,
  include?: UniqueList<ObjectRef>[0..*],
  exclude?: UniqueList<ObjectRef>[0..*]
} where match is present or include contains at least one object

TagAssignment = {
  securable: SecurableRef,
  tags: FrameworkTagMap<securable.type>
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
TagsYaml = {
  schema_version: SchemaVersion,
  tags: List<TagAssignment>[0..*]
}
```

## Notes

- The framework-managed key vocabulary, each key's allowed values, and each key's allowed
  securable grains are closed JSON Schema `enum` definitions, not free-form strings. The
  schemas enumerate the approved vocabulary for the release.
- `acm_business_function` is one asset-tag example. `acm_pii_type` and `acm_rls` are the
  special column-routing keys used by this contract.
- For `ObjectRef` and `SecurableRef`, catalogs use `CatalogName`, schemas use `SchemaName`,
  columns use `ColumnName`, and all other types use `ObjectName`.
- Tags outside the `acm_` namespace are not selected, assigned, or removed by this framework.

[JSON Schema 2020-12]: https://json-schema.org/draft/2020-12
