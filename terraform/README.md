# `terraform/` — provision account groups + env schemas, as two separate roots

> **Primary path for DE teams: the Python + `databricks` CLI provisioner**
> (`ucgov-provision groups|schemas`, from the `eastspring-uc-governance` wheel — see its `--help`). It reads the same YAML
> with the same split. This Terraform is kept as an **alternative** for teams that use Terraform.
> Two behavioural differences:
> - **Access:** the CLI provisioner needs **no account-level access** — it reaches account groups
>   through the workspace-level IAM v2 *proxy* API (a **workspace admin** profile). This Terraform's
>   `account/` root uses the account-level provider and needs **account admin**.
> - **Deletes:** Terraform **drops** schemas / local groups removed from the YAML; the CLI
>   provisioner is **additive only** and never deletes.

Terraform owns a slice of the **Unity Catalog containers + identity** (per the design split). It is
split by **provider level**, because the two halves need different auth:

| Root | Provisions | Reads | Auth / rights |
|---|---|---|---|
| `account/` | account **groups** | `config/reference/<env>.yaml` `groups` | account profile, **account admin** |
| `workspace/` | governance + serving **schemas** | `config/reference/<env>.yaml` | workspace profile, `CREATE SCHEMA` |

Both roots read the env's reference YAML directly (`yamldecode`) — the same file the framework and
validator use, so nothing is duplicated — and take `-var env=<env>`. Each root has its own state
and lock file. Apply `account/` first: grants in an env fail if the groups they name don't exist yet.

What it does **not** do (by design): create the **`target_catalog`** (customer / metastore-admin
pre-creates it; we only reference it by name); the **`source_schemas`** (gold/silver) — those hold DE-owned
base tables the framework only reads from, so they are not provisioned here; grants, tags,
column-mask policies, dynamic views, UDFs, the `gpa` MV (all **framework**); and the
`entitlements`/`entitlement_mapping` + base-table data (**customer**). See the ownership split in
the project docs.

## `account/` — account groups
Each `config/reference/<env>.yaml` `groups` entry's **`external_id`** picks how the group is
provisioned:
- **set (AIM):** resolved from Microsoft Entra ID by that Entra **Object ID**, via the
  `databricks_account_iam_external_group_v2` data source (IAM v2 API, Beta). Resolving
  **provisions** the group into the account if it isn't there — AIM makes Entra groups searchable
  but does not add them on its own. Fails closed if the GUID resolves to an Entra group whose name
  differs from the reference name.
- **null (local):** a plain Databricks-managed `databricks_group`, no Entra link — the dev path
  that replaces the old `seed_esg_groups.sh`.

```bash
cd terraform/account
terraform init
terraform plan  -var env=esg          # -var databricks_account_profile=<account profile>
terraform apply -var env=esg
```
- **Prereqs:** account-admin rights. For any group with an `external_id`, the account must be
  **onboarded to AIM** and the group must **already exist in Entra ID**.
- **No account access?** Use the CLI provisioner instead (`eastspring_uc_governance.provision.cli groups`), which
  reaches the same account groups through the workspace proxy API with a workspace-admin profile.
- **Groups shared by several envs:** each env lists the group in its own reference. With one
  Terraform state per env, two envs managing the same local group would both try to own it — use
  `force = true` adoption (already set) knowingly, or manage shared groups from one env only.

## `workspace/` — env schemas
Schemas are the **union** of `reference.governance_schemas` + `serving_schemas`; `source_schemas`
are deliberately excluded (DE-owned base tables). Only entries under `target_catalog` are kept and
the bare schema name is derived by stripping the `<catalog>.` prefix. (The validator additionally
warns if a dynamic view's target schema isn't a declared `serving_schema`.)

```bash
cd terraform/workspace
terraform init
terraform plan  -var env=esg          # preview: N schemas (catalog referenced, not created)
terraform apply -var env=esg
```
- **Prereqs:** the **`target_catalog` must already exist** (customer / metastore admin creates it)
  and you need `CREATE SCHEMA` / `USE CATALOG` on it.
- **Per-env state:** one state per env (`terraform workspace` or a backend key per env) — the
  `env` var picks the reference file, not the state.

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
- **Destroy:** `terraform destroy` in `workspace/` drops the schemas (not the catalog); a non-empty
  schema needs `-var force_destroy=true`. In `account/` it drops the local groups only.
- Adding a schema to `config/reference/<env>.yaml` and re-applying creates it; removing one and
  re-applying **drops** it (Terraform-managed) — mind that `dm_esg` etc. hold framework-created views.
