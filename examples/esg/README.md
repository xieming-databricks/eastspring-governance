# Example: ESG (`hera-dev`)

A complete, working deployment — laid out exactly like the configs repo — that runs end to end in
an **empty dev workspace**. Use it to try the framework, or copy it as the starting point for a new
domain.

```
excels/esg/access-matrix-esg_workshop-v3-dynamic-views.xlsx   the Access Matrix (input)
config/esg/*.yaml          generated from the workbook — never edit by hand
reference/esg.yaml         target_catalog hera-dev; every group's external_id is null
sql/esg/*.sql              mask UDFs + the group_portfolio_access RLS MV
dev-tools/seed_esg.sql     catalog, source + governance schemas, base tables (sample data)
dev-tools/seed_esg_rls.sql the two RLS entitlement tables (sample data)
```

`external_id: null` everywhere means `ucgov-provision groups` creates **local** account groups, so
the example doesn't depend on any Entra tenant. A real deployment keeps its own reference, with the
groups' Entra object IDs, in the configs repo.

## Check the example

`tests/test_example.py` checks this on every build (plus that every table, column, schema,
function and RLS group the config refers to is created by the seeds, provisioning or `sql/esg`).
By hand, after changing anything here:

```bash
ucgov-generate examples/esg/excels/esg/access-matrix-esg_workshop-v3-dynamic-views.xlsx examples/esg/config/esg --verify
ucgov-validate examples/esg/config/esg --reference examples/esg/reference/esg.yaml --sql examples/esg/sql/esg
```

`--verify` fails if `config/esg` is out of date with the workbook. Regenerate with the same command
without `--verify`, then commit both.

## Run it end to end

You need workspace admin on a Unity Catalog workspace, a SQL warehouse, serverless jobs and
ABAC-capable compute. Run from the repo root, with the package installed (`pip install -e .`)
and a CLI profile for the workspace (`P=<workspace-profile>`).

1. **Seed** — run `dev-tools/seed_esg.sql`, then `dev-tools/seed_esg_rls.sql`, in the SQL editor.
2. **Provision** — plan first (read-only), then apply:
   ```bash
   for what in groups schemas tags; do
     ucgov-provision $what --reference examples/esg/reference/esg.yaml --profile $P            # plan
     ucgov-provision $what --reference examples/esg/reference/esg.yaml --profile $P --apply    # apply
   done
   ```
   Governed tags are account-wide: if `acm_pii_type` already exists in the account, the plan only
   adds the missing values (it never removes any).
3. **Upload** — the wheel, the notebooks and the example (the `.sql` files as plain files):
   ```bash
   R=/Workspace/Users/<you>/ucgov-example
   pip wheel . --no-deps -w dist
   databricks workspace mkdirs $R/wheels -p $P
   databricks workspace import $R/wheels/$(ls dist) --file dist/$(ls dist) --format AUTO --overwrite -p $P
   databricks workspace import-dir notebooks $R/notebooks --overwrite -p $P
   databricks workspace import-dir examples/esg/config/esg $R/config/esg --overwrite -p $P
   databricks workspace mkdirs $R/reference -p $P
   databricks workspace import $R/reference/esg.yaml --file examples/esg/reference/esg.yaml --format AUTO --overwrite -p $P
   databricks workspace mkdirs $R/sql/esg -p $P
   for f in examples/esg/sql/esg/*.sql; do
     databricks workspace import $R/sql/esg/$(basename $f) --file $f --format RAW --overwrite -p $P
   done
   ```
4. **Functions + RLS allow-list** — open `$R/notebooks/apply_functions`, add the uploaded wheel as a
   dependency in the notebook's **Environment** panel, and run it with `reference_path` =
   `$R/reference/esg.yaml`, `sql_path` = `$R/sql/esg`: first `mode=plan`, then `mode=apply`.
   - Creating the MV from serverless notebook compute needs the workspace preview *Serverless
     Generic Compute Materialized View/Streaming Table*. Without it the MV fails (the mask UDFs
     still succeed): enable the preview, or run the MV's SQL (as rendered in the plan output) on a
     SQL warehouse.
5. **Reconcile** — open `$R/notebooks/reconcile` (same Environment), with `config_path` =
   `$R/config/esg`, `reference_path` = `$R/reference/esg.yaml`: `mode=plan`, review, `mode=apply`,
   then `mode=plan` again — it should find nothing left to do.

Expected end state: 4 column-mask policies and 7 column tags on `hera-dev.gold` / `silver`, 11
dynamic views in `hera-dev.dm_esg` / `dm_something` (RLS on the `portfolio_code` views), and the
grants for the six `GSGEIS-*` groups. Add users to the groups to see masking and RLS per group.

## Clean up

```sql
DROP CATALOG `hera-dev` CASCADE;
```
```bash
# the local groups the example created (they are account-level; provisioning never deletes)
databricks workspace-iam-v2 list-groups-proxy -p $P -o json \
  | python3 -c "import json,sys; [print(g['group_id'], g['group_name']) for g in json.load(sys.stdin) if g['group_name'].startswith('GSGEIS-hera-')]"
databricks workspace-iam-v2 delete-group-proxy <group_id> -p $P
```
The governed tag `acm_pii_type` is account-wide and may be used elsewhere — leave it.

## Use it as a template

Copy `examples/esg/` into the configs repo as a new domain (`excels/<env>/`, `config/<env>/`,
`reference/<env>.yaml`, `sql/<env>/`), then edit the workbook and the reference (target catalog,
schemas, groups with their Entra object IDs) and regenerate `config/<env>/`. The `dev-tools/` seeds
are for the example's `hera-dev` catalog only.
