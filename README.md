# eastspring-uc-governance

YAML-driven Unity Catalog governance for Eastspring: an Excel **Access Matrix** becomes validated
YAML, which is reconciled against the live workspace — grants, column tags, ABAC column-mask
policies and dynamic (RLS) views — with a reviewable **plan** before every **apply**.

## Two repositories

| Repository | Holds | Built / run by |
|---|---|---|
| **this repo** — the engine | the `eastspring_uc_governance` Python package (the wheel), the Databricks notebooks, the job definitions | `azure-pipelines.yml`: test, build + publish the wheel, deploy the notebooks, create / update the jobs |
| **eastspring-governance-configs** — the deployment content | `excels/<env>/` (the workbook), `config/<env>/` (generated YAML), `reference/<env>.yaml` (the allow-list), `sql/<env>/` (mask UDFs + RLS allow-list MV) | `azure-pipelines-platform.yml` (provisioning) and `azure-pipelines.yml` (reconcile) |

The wheel contains no environment content: no SQL, no config, no group names. Everything
Eastspring-specific lives in the configs repo and is passed in by path — never guessed.
[`examples/esg/`](examples/esg/README.md) is a working copy of the ESG deployment, laid out like the
configs repo, that runs end to end in an empty dev workspace.

```
src/eastspring_uc_governance/
  generate/     workbook (.xlsx) -> config YAML                       (ucgov-generate)
  validation/   JSON-Schema + semantic + reference + SQL-folder checks (ucgov-validate)
  provision/    groups, schemas, governed tags via the databricks CLI  (ucgov-provision)
  compile/      config -> target state (grants / tags / policies)
  reconcile/    live state, diff, preflight checks
  apply/        DDL generation + phased execution
  sql_objects.py  renders the configs repo's SQL templates for one env
  schemas/config/v1/  the YAML contract (CONTRACT.md + *.schema.yaml)
notebooks/      reconcile.py, apply_functions.py   (run as Databricks jobs)
jobs/           ucgov_reconcile.json, ucgov_apply_functions.json
examples/esg/   a complete ESG deployment (workbook, config, reference, SQL, dev seeds) — see its README
tests/          self-contained unit tests (fixtures under tests/fixtures/)
```

## What runs where

**Platform pipeline** (configs repo, `azure-pipelines-platform.yml`) — what the reconcile needs to
exist first; runs when `reference/` or `sql/` change:

1. `ucgov-validate` — config + reference + SQL folder
2. `ucgov-provision groups` — account groups from the reference (`external_id` set: resolved from
   Entra ID via AIM; `null`: a local account group). Needs **workspace admin**.
3. `ucgov-provision schemas` — the governance + serving schemas under `target_catalog`
4. `ucgov-provision tags` — the reference's `governed_tags` as governed tags (tag policies)
5. job **`ucgov-apply-functions`** — `CREATE OR REPLACE` the mask UDFs + the
   `group_portfolio_access` MV from `sql/<env>/`, then checks that every group in the RLS
   allow-list is a reference group

Provisioning is **additive only**: nothing is ever deleted, a governed tag value is never removed.

**Config pipeline** (configs repo, `azure-pipelines.yml`) — on every config change:

1. `ucgov-validate`
2. a read-only provisioning check (groups / schemas / governed tags exist)
3. deploy the config + reference to a dated workspace folder
4. job **`ucgov-reconcile`** — plan for pull requests; on `main`, the `mode` run parameter

Both pipelines take a `mode` parameter (`plan` | `apply`). Pull requests always plan; apply runs
only from `main`. Every run publishes the notebook output as an HTML artifact.

### Reconcile: full-sync, tightened first

Within `target_catalog` the config is authoritative: an undeclared grant is a REMOVE (except the
protected set: the deployer, `admin_groups`, `protected_schemas`); tags and policies are managed
only in the `acm_` namespace. The apply runs in phases, so data is never readable unmasked while
it changes:

| Phase | Statements |
|---|---|
| masking | column-mask policies ADD/CHANGE, then column tags ADD/CHANGE |
| views | `CREATE OR REPLACE` / `DROP` the dynamic views |
| access | grants ADD, then REMOVE |
| loosen | `UNSET TAGS`, then `DROP POLICY` |

A failed masking statement skips everything after it; any FAILED / SKIPPED statement fails the job.
The run stops **before any change** if the RLS allow-list table or a view's schema is missing, or
if `system.information_schema.abac_policy_definitions` can't be read.

## Commands

```bash
ucgov-generate excels/esg/<workbook>.xlsx config/esg [--check | --verify]
ucgov-validate config/esg --reference reference/esg.yaml --sql sql/esg [--verbose] [--skip-warning]
ucgov-provision groups|schemas|tags --reference reference/esg.yaml [--profile <workspace-profile>] [--apply]
```

`--check` validates the workbook without writing; `--verify` fails unless `config/esg` already holds
exactly what the workbook generates. `--reference` and `--sql` are required. Validation warnings
fail by default (`--skip-warning` to allow them). `ucgov-provision` only reads unless `--apply` is given.

## Prerequisites (one-time, outside the framework)

- **Catalog** `target_catalog` and the **source schemas / base tables** — customer-owned.
- **RLS entitlement tables** `<catalog>.<governance>.entitlements` and `entitlement_mapping` —
  customer-owned; the MV only reads them. Their `ad_group` values must be the reference's group
  names exactly (a stale name grants nothing; `ucgov-apply-functions` warns about it).
- **Entra ID groups** (for `external_id` groups) and **AIM** enabled; group **membership** is
  managed in Entra ID.
- **Grants for the pipeline identity**: workspace admin (groups), `CREATE SCHEMA` on the catalog,
  permission to manage governed tags, `ASSIGN` on the governed tags, `MANAGE` on the catalog.
- **Compute**: serverless jobs; ABAC-capable compute for policy DDL. Creating the MV from serverless
  notebook compute needs the workspace preview *Serverless Generic Compute Materialized
  View/Streaming Table* (a SQL warehouse can create it without the preview).

## RLS model — how `group_portfolio_access` is derived

Customer-maintained input 1, **`entitlements`** — each group's scope per axis (one row per axis):

| `ad_group` | scope |
|---|---|
| `GSGEIS-hera-all-esg-read-unmasked` | `region=*`, `team=*` |
| `GSGEIS-hera-all-esg-read-masked` | `region=*`, `team=*` |
| `GSGEIS-hera-sg-esg-read-masked` | `region=EISG`, `team=*` |
| `GSGEIS-hera-sg-eq-read-masked` | `region=EISG`, `team=EQ` |
| `GSGEIS-hera-sg-fi-read-masked` | `region=EISG`, `team=FI` |

Customer-maintained input 2, **`entitlement_mapping`** — which `portfolio_code` carries each value:

| `type` | `value` | `portfolio_code` |
|---|---|---|
| region | `EISG` | PF_EQ_1, PF_EQ_2, PF_FI_1 |
| region | `AMER` | PF_OTHER |
| team | `EQ` | PF_EQ_1, PF_EQ_2 |
| team | `FI` | PF_FI_1 |
| team | `OTHER` | PF_OTHER |

A group reaches a code on an axis if its value is `*` or the code carries that value; the MV keeps
only codes reached on **both** axes (region ∩ team) and fails closed when a group lacks an axis:

| `ad_group` | `portfolio_code` |
|---|---|
| `GSGEIS-hera-all-esg-read-unmasked` | PF_EQ_1, PF_EQ_2, PF_FI_1, PF_OTHER |
| `GSGEIS-hera-all-esg-read-masked` | PF_EQ_1, PF_EQ_2, PF_FI_1, PF_OTHER |
| `GSGEIS-hera-sg-esg-read-masked` | PF_EQ_1, PF_EQ_2, PF_FI_1 |
| `GSGEIS-hera-sg-eq-read-masked` | PF_EQ_1, PF_EQ_2 |
| `GSGEIS-hera-sg-fi-read-masked` | PF_FI_1 |

Each RLS view (a `rbac_views.yaml` entry with `rls_column`) filters as the querying user:

```sql
WHERE portfolio_code IN (SELECT portfolio_code FROM `hera-dev`.governance.group_portfolio_access
                         WHERE is_account_group_member(ad_group))
```

The MV is `TRIGGER ON UPDATE`: it refreshes on its own when the entitlement tables change.

## Development

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"                                # + ",spark" for tests/test_diff.py (Java 17, Python 3.12/3.13)
python -m pytest tests/
pip wheel . --no-deps -w dist                          # the wheel the pipeline publishes
```

The tests are self-contained (`tests/fixtures/`) and never read the configs repo — except
`tests/test_example.py`, which keeps `examples/esg` a working deployment: its config is what its
workbook generates, it validates cleanly, and every table, column, schema, function and RLS group it
refers to is created by the example itself. To try it in a dev workspace, follow
[examples/esg/README.md](examples/esg/README.md).
