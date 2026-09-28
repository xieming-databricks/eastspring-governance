# `terraform/` — provision the catalog + schemas + account groups from `config/reference/<env>.yaml`

Terraform owns the **Unity Catalog containers + identity** (per the design split): the
`target_catalog`, the `schemas`, and the account `groups` declared in the deployment's reference
allow-list. It reads that YAML directly (`yamldecode`), so the set stays in sync with the same file
the framework and validator use — no duplication.

- `main.tf` — `databricks_catalog` + `databricks_schema` (workspace provider).
- `groups.tf` — `databricks_group` (account provider). **AIM is enabled**, so each group is linked to
  its Microsoft Entra ID group via `external_id`: we look the group up in Entra by display name
  (`azuread_group` data source) and set `external_id = <object id>`. A group missing/ambiguous in
  Entra fails the plan (fail-closed). Toggle with **`entra_linked_groups`** (default `true`):
  set it `false` to create plain **local** Databricks account groups (no Entra, no `az login`) —
  the dev path that replaces the old `seed_esg_groups.sh`.

What it does **not** do (by design): grants, tags, column-mask policies, dynamic views, UDFs, the
`gpa` MV (all **framework**), and the `entitlements`/`entitlement_mapping` + base-table data
(**customer**). See the ownership split in the project docs.

## Usage
```bash
cd terraform
terraform init
terraform plan  -var env=esg          # preview: 1 catalog + N schemas + M groups
terraform apply -var env=esg
```
- **Auth (three providers):**
  - workspace `databricks` → `databricks_profile` var (default `xieming-azure`) — catalog/schemas.
  - account `databricks.account` → `databricks_account_profile` var (default `azure-account-ccb842e7`, must be an account-level profile) — groups.
  - `azuread` → Azure CLI (`az login`) by default (or a service principal via the AzureAD provider env vars) — Entra group lookups. **Only needed when `entra_linked_groups = true`.**
- **Prereqs:** metastore attached + `CREATE CATALOG`/`CREATE SCHEMA`; account-admin rights to create
  groups. When `entra_linked_groups = true`, the `hera-*` groups must **already exist in Entra ID**
  (their `external_id` is resolved from there) and you need `az login`; when `false`, neither applies.
  If the metastore has no default managed storage, pass `-var storage_root=abfss://…`.
- **Local dev (no Entra):** `terraform apply -var env=esg -var entra_linked_groups=false` creates the
  account groups with no external identity — this is the retired `seed_esg_groups.sh` path.
- **Selectivity:** only `reference.schemas` entries under `target_catalog` are created; the bare
  schema name is derived by stripping the `<catalog>.` prefix. Groups come straight from
  `reference.groups`.

## Caveats
- **Existing objects:** if the catalog/schemas already exist (e.g. seeded via
  `dev-tools/seed_esg.sql` in a dev workspace), `apply` will conflict — either `terraform import`
  them into state, or drop the seeded objects first. This module is the *authoritative* creator, so
  in prod it should be the only thing that makes the catalog/schemas.
- **Destroy:** `terraform destroy` won't drop a non-empty catalog unless `-var force_destroy=true`.
- Adding a schema to `config/reference/<env>.yaml` and re-applying creates it; removing one and
  re-applying **drops** it (Terraform-managed) — mind that `dm_esg` etc. hold framework-created views.
