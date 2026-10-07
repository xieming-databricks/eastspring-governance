# `terraform/` — provision the schemas + account groups from `config/reference/<env>.yaml`

Terraform owns a slice of the **Unity Catalog containers + identity** (per the design split): the
**governance + serving `schemas`** and the account `groups` declared in the deployment's reference
allow-list. It reads that YAML directly (`yamldecode`), so the set stays in sync with the same file
the framework and validator use — no duplication. The **`target_catalog` is NOT created here** — it
is a customer / metastore-admin-owned container, pre-created outside this module; Terraform just
creates the schemas under it by name (if the catalog is absent, the schema create fails at apply).

- `main.tf` — `databricks_schema` under the (pre-existing) `target_catalog` (workspace provider).
- `groups.tf` — account groups (account provider). Each `reference.groups` entry's **`external_id`**
  picks how it is provisioned (per group — no global switch):
  - **set (AIM):** resolved from Microsoft Entra ID by that Entra **Object ID**, via the
    `databricks_account_iam_external_group_v2` data source (IAM v2 API, Beta). Resolving
    **provisions** the group into the account if it isn't there — AIM makes Entra groups searchable
    but does not add them on its own. Fails closed if the GUID resolves to an Entra group whose name
    differs from the reference name.
  - **null (local):** a plain Databricks-managed `databricks_group`, no Entra link — the dev path
    that replaces the old `seed_esg_groups.sh`.

What it does **not** do (by design): create the **`target_catalog`** (customer / metastore-admin
pre-creates it; we only reference it by name); the **`source_schemas`** (gold/silver) — those hold DE-owned
base tables the framework only reads from, so they are not provisioned here; grants, tags,
column-mask policies, dynamic views, UDFs, the `gpa` MV (all **framework**); and the
`entitlements`/`entitlement_mapping` + base-table data (**customer**). See the ownership split in
the project docs.

## Usage
```bash
cd terraform
terraform init
terraform plan  -var env=esg          # preview: N schemas + M groups (catalog referenced, not created)
terraform apply -var env=esg
```
- **Auth (two providers, no Azure CLI needed):**
  - workspace `databricks` → `databricks_profile` var (default `xieming-azure`) — schemas.
  - account `databricks.account` → `databricks_account_profile` var (default `azure-account-ccb842e7`, must be an account-level profile) — groups.
- **Prereqs:** the **`target_catalog` must already exist** (customer / metastore admin creates it) and
  you need `CREATE SCHEMA` / `USE CATALOG` on it; account-admin rights for groups. For any group
  with an `external_id`, the account must be **onboarded to AIM** and the group must **already
  exist in Entra ID**.
- **Local dev (no Entra):** leave `external_id: null` — those groups are created locally, with no
  external identity.
- **Selectivity:** schemas are the **union** of `reference.governance_schemas` + `serving_schemas`
  (created); `source_schemas` are deliberately excluded (DE-owned base tables). Only entries under
  `target_catalog` are kept and the bare schema name is derived by stripping the `<catalog>.` prefix.
  Groups are the `name`s in the `reference.groups` list. (The validator additionally warns if a dynamic view's
  target schema isn't a declared `serving_schema`.)

## Caveats
- **Catalog is referenced by name, not managed:** the `target_catalog` must exist before `apply`, or
  the first `databricks_schema` create fails (fail-closed). Terraform never creates, alters, or
  destroys the catalog — that is the customer's / metastore admin's job.
- **Existing schemas:** if a governance/serving schema already exists but isn't in state (e.g. seeded
  via `dev-tools/seed_esg.sql` in a dev workspace), `apply` will conflict — either `terraform import
  'databricks_schema.this["<name>"]' <catalog>.<name>` into state, or drop the seeded schema first.
  For the catalog itself there is no conflict, since we never create it.
- **AIM groups are resolved by a data source:** provisioning happens at `plan` (a read with a side
  effect), and `terraform destroy` does **not** remove AIM groups.
- **Linking an existing local group** (changing its `external_id` from null to a GUID): the AIM group
  is resolved at `plan`, while the local `databricks_group` of the same name is only destroyed at
  `apply`. How AIM resolve behaves while a same-named local group still exists is **unverified** —
  delete the local group first.
- **Destroy:** `terraform destroy` drops the schemas (not the catalog); a non-empty schema needs
  `-var force_destroy=true`.
- Adding a schema to `config/reference/<env>.yaml` and re-applying creates it; removing one and
  re-applying **drops** it (Terraform-managed) — mind that `dm_esg` etc. hold framework-created views.
