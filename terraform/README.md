# `terraform/` — provision the schemas + account groups from `config/reference/<env>.yaml`

Terraform owns a slice of the **Unity Catalog containers + identity** (per the design split): the
**governance + serving `schemas`** and the account `groups` declared in the deployment's reference
allow-list. It reads that YAML directly (`yamldecode`), so the set stays in sync with the same file
the framework and validator use — no duplication. The **`target_catalog` is NOT created here** — it
is a customer / metastore-admin-owned container, pre-created outside this module; Terraform just
creates the schemas under it by name (if the catalog is absent, the schema create fails at apply).

- `main.tf` — `databricks_schema` under the (pre-existing) `target_catalog` (workspace provider).
- `groups.tf` — `databricks_group` (account provider). **AIM is enabled**, so each group is linked to
  its Microsoft Entra ID group via `external_id`: we look the group up in Entra by display name
  (`azuread_group` data source) and set `external_id = <object id>`. A group missing/ambiguous in
  Entra fails the plan (fail-closed). Toggle with **`entra_linked_groups`** (default `true`):
  set it `false` to create plain **local** Databricks account groups (no Entra, no `az login`) —
  the dev path that replaces the old `seed_esg_groups.sh`.

What it does **not** do (by design): create the **`target_catalog`** (customer / metastore-admin
pre-creates it; we only look it up); the **`source_schemas`** (gold/silver) — those hold DE-owned
base tables the framework only reads from, so they are not provisioned here; grants, tags,
column-mask policies, dynamic views, UDFs, the `gpa` MV (all **framework**); and the
`entitlements`/`entitlement_mapping` + base-table data (**customer**). See the ownership split in
the project docs.

## Usage
```bash
cd terraform
terraform init
terraform plan  -var env=esg          # preview: N schemas + M groups (catalog looked up, not created)
terraform apply -var env=esg
```
- **Auth (three providers):**
  - workspace `databricks` → `databricks_profile` var (default `xieming-azure`) — catalog lookup + schemas.
  - account `databricks.account` → `databricks_account_profile` var (default `azure-account-ccb842e7`, must be an account-level profile) — groups.
  - `azuread` → Azure CLI (`az login`) by default (or a service principal via the AzureAD provider env vars) — Entra group lookups. **Only needed when `entra_linked_groups = true`.**
- **Prereqs:** the **`target_catalog` must already exist** (customer / metastore admin creates it) and
  you need `CREATE SCHEMA` / `USE CATALOG` on it; account-admin rights to create groups. When
  `entra_linked_groups = true`, the `hera-*` groups must **already exist in Entra ID**
  (their `external_id` is resolved from there) and you need `az login`; when `false`, neither applies.
- **Local dev (no Entra):** `terraform apply -var env=esg -var entra_linked_groups=false` creates the
  account groups with no external identity — this is the retired `seed_esg_groups.sh` path.
- **Selectivity:** schemas are the **union** of `reference.governance_schemas` + `serving_schemas`
  (created); `source_schemas` are deliberately excluded (DE-owned base tables). Only entries under
  `target_catalog` are kept and the bare schema name is derived by stripping the `<catalog>.` prefix.
  Groups come straight from `reference.groups`. (The validator additionally warns if a dynamic view's
  target schema isn't a declared `serving_schema`.)

## Caveats
- **Catalog is referenced by name, not managed:** the `target_catalog` must exist before `apply`, or
  the first `databricks_schema` create fails (fail-closed). Terraform never creates, alters, or
  destroys the catalog — that is the customer's / metastore admin's job.
- **Existing schemas:** if a governance/serving schema already exists but isn't in state (e.g. seeded
  via `dev-tools/seed_esg.sql` in a dev workspace), `apply` will conflict — either `terraform import
  'databricks_schema.this["<name>"]' <catalog>.<name>` into state, or drop the seeded schema first.
  For the catalog itself there is no conflict, since we only look it up.
- **Destroy:** `terraform destroy` drops the schemas (not the catalog); a non-empty schema needs
  `-var force_destroy=true`.
- Adding a schema to `config/reference/<env>.yaml` and re-applying creates it; removing one and
  re-applying **drops** it (Terraform-managed) — mind that `dm_esg` etc. hold framework-created views.
