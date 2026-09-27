# Databricks notebook source
# MAGIC %md
# MAGIC # Governance reconcile — full-sync a target catalog against the YAML config
# MAGIC
# MAGIC Compiles the validated YAML into target state, reads live state from
# MAGIC `system.information_schema` (+ `SHOW POLICIES`), emits a **per-dimension delta**
# MAGIC (`change_type` = ADD / REMOVE / CHANGE) + a **warning list**, and — in the apply
# MAGIC stage — executes the grant/tag delta as GRANT / REVOKE / ALTER…SET/UNSET TAGS.
# MAGIC
# MAGIC **v1 full-sync scope:** the **`target_catalog`** is treated as framework-owned —
# MAGIC every live grant in it that the YAML does not declare is a **REMOVE**, except grants
# MAGIC to a **protected principal** (the deploying identity + `admin_groups`) or in a
# MAGIC **protected schema** (`information_schema` + extras). Tags/policies stay
# MAGIC `acm_`-namespaced. Anything outside the target catalog is ignored.
# MAGIC
# MAGIC **`config_path`:** workspace path holding `base_grants.yaml` / `labels.yaml` /
# MAGIC `tags.yaml` / `policies.yaml` (+ optional `rbac_view_grants.yaml`, `rbac_views.yaml`).
# MAGIC
# MAGIC **Reference allow-list** (`../reference/<config_dir_name>.yaml`, sibling of the config
# MAGIC dir): hand-curated per-deployment source of truth for the **`target_catalog`**, the
# MAGIC **protected set** (`protected_principals` = `deployers` + `admin_groups`, plus
# MAGIC `protected_schemas`), and the approved vocabulary (groups / schemas / governed_tags)
# MAGIC that off-list use warns against.
# MAGIC
# MAGIC **`mode`:** `plan` (default) previews the SQL and changes nothing; `apply`
# MAGIC executes the grant + tag + column-mask policy statements (`CREATE OR REPLACE`/`DROP
# MAGIC POLICY` — requires **ABAC-capable compute**).

# COMMAND ----------

dbutils.widgets.text("config_path", "/Workspace/Users/xieming.li@databricks.com/eastspring-governance/config/esg")
# Leave target_catalog blank to use the reference allow-list's `target_catalog` (the source of
# truth); set it only to override the reference for an ad-hoc run.
dbutils.widgets.text("target_catalog", "", "target catalog (optional override of reference allow-list)")
dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"])

import sys
from pathlib import Path

# Repo root = parent of notebooks/. Contains framework/, config/, schemas/ as siblings.
NOTEBOOK_DIR = Path("/Workspace") / Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent.relative_to("/")
REPO_ROOT = NOTEBOOK_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
print(f"[reconcile] repo_root={REPO_ROOT}")


def show(df):
    """display() in Databricks, .show() elsewhere."""
    try:
        display(df)  # noqa: F821  (Databricks builtin)
    except Exception:
        df.show(truncate=False)

# COMMAND ----------

# DBTITLE 1,Load config from the workspace path (+ reference allow-list)
from pathlib import Path
import yaml

config_path = Path(dbutils.widgets.get("config_path"))
# base_grants/labels/tags/policies are mandatory; labels.yaml holds asset labels for grant
# matching (never materialized to UC).
docs = {
    k: yaml.safe_load((config_path / f"{k}.yaml").read_text())
    for k in ("base_grants", "labels", "tags", "policies")
}
# rbac_view_grants.yaml (serving-schema grants) and rbac_views.yaml (expected dynamic views)
# are optional; load if present.
for opt in ("rbac_view_grants", "rbac_views"):
    opt_path = config_path / f"{opt}.yaml"
    if opt_path.exists():
        docs[opt] = yaml.safe_load(opt_path.read_text())

# Reference allow-list: authoritative, hand-curated per-deployment vocabulary + the target
# catalog + full-sync protected set. Lives at ../reference/<config_dir_name>.yaml (sibling of
# the config dir), NOT generated from the workbook. Optional: absent -> warning layer skipped
# and target_catalog must come from the widget.
reference_path = config_path.parent / "reference" / f"{config_path.name}.yaml"
reference = {}
if reference_path.exists():
    reference = yaml.safe_load(reference_path.read_text()) or {}

# Governance operational config (sibling ../governance/<name>.yaml): holds mask_exempt_principals
# (ETL identities added to EXCEPT on every column-mask policy so pipelines read unmasked data).
governance_path = config_path.parent / "governance" / f"{config_path.name}.yaml"
governance = {}
if governance_path.exists():
    governance = yaml.safe_load(governance_path.read_text()) or {}
mask_exempt_principals = governance.get("mask_exempt_principals") or []
print(f"[reconcile] config_source={config_path} | "
      f"rbac_view_grants={'yes' if 'rbac_view_grants' in docs else 'no'} | "
      f"rbac_views={'yes' if 'rbac_views' in docs else 'no'} | "
      f"reference={'yes' if reference else 'no'} ({reference_path.name}) | "
      f"mask_exempt_principals={mask_exempt_principals}")

# COMMAND ----------

# DBTITLE 1,Validate the config (fail-closed errors; fail-open reference warnings)
from framework.validation import validate_docs

# Passing the reference adds fail-open warnings for any group / catalog.schema / tag key+value
# the config uses but the allow-list does not list. Warnings are reported, never block.
vres = validate_docs(docs, reference=reference or None)
for issue in vres.sorted_issues():
    print(f"  {issue}")
if not vres.ok:
    raise ValueError(f"config invalid: {len(vres.errors)} error(s) — fix before reconciling")
if vres.warnings:
    print(f"[reconcile] config valid — {len(vres.warnings)} reference warning(s) (not blocking)")
else:
    print("[reconcile] config valid — no reference warnings")

# COMMAND ----------

# DBTITLE 1,Compile -> target state (grants / tags / policies + referenced objects)
from framework.compile import compile_config

# mask_exempt_principals is stamped onto each column-mask policy's EXCEPT clause (target state),
# so an exempt-list change surfaces as a policy CHANGE in the diff.
state = compile_config(docs, mask_exempt_principals=mask_exempt_principals)
if not state.ok:
    for err in state.errors:
        print(f"  {err}")
    raise ValueError(f"compile failed: {len(state.errors)} error(s)")

print(f"[reconcile] target: {len(state.grants)} grants | {len(state.tags)} tags | "
      f"{len(state.policies)} policies | {len(state.refs)} referenced objects")

# COMMAND ----------

# DBTITLE 1,Target catalog + target-state DataFrames
from framework.reconcile import frames, scope

# Target catalog: reference allow-list is the source of truth; a non-empty widget overrides it
# for ad-hoc runs.
target_catalog = dbutils.widgets.get("target_catalog").strip() or (reference.get("target_catalog") or "").strip()
if not target_catalog:
    raise ValueError("no target_catalog: set the widget or `target_catalog` in the reference allow-list")
print(f"[reconcile] target_catalog source={'widget' if dbutils.widgets.get('target_catalog').strip() else 'reference'}")
catalogs = [target_catalog]

# Anything outside the target catalog is ignored by the diff — surface how much.
outside = sorted({g.securable_fqn for g in state.grants
                  if g.securable_fqn.split(".")[0] != target_catalog}
                 | {t.securable_fqn for t in state.tags
                    if t.securable_fqn.split(".")[0] != target_catalog}
                 | {p.scope for p in state.policies
                    if p.scope.split(".")[0] != target_catalog})
print(f"[reconcile] target_catalog={target_catalog}")
if outside:
    print(f"[reconcile] ignoring {len(outside)} securable(s) outside the target catalog: {outside}")

target = frames.target_frames(spark, state)  # noqa: F821  (spark builtin)
for dim, df in target.items():
    print(f"target {dim}: {df.count()} rows")
    show(df)

# COMMAND ----------

# DBTITLE 1,Read live state from information_schema (grants/tags/policies) — as DataFrames
from framework.reconcile import live

live_frames = {
    "grants": live.read_live_grants(spark, catalogs),
    "tags": live.read_live_tags(spark, catalogs),
    "policies": live.read_live_policies(spark, catalogs),
}
existing = live.read_existing_objects(spark, catalogs)                       # noqa: F821
print(f"[reconcile] live objects for preflight: {len(existing)}")

# COMMAND ----------

# DBTITLE 1,Protected set (full-sync guardrail — never proposed for REMOVE)
# The reference allow-list is the sole home of the protected set: principals (deployers +
# admin_groups) nested under `protected_principals`, and `protected_schemas` top-level.
deployer = spark.sql("SELECT current_user()").collect()[0][0]  # noqa: F821
ref_pp = reference.get("protected_principals") or {}

protected_principals = scope.protected_principals(
    deployer=deployer,
    deployers=ref_pp.get("deployers"),
    admin_groups=ref_pp.get("admin_groups"))
protected_schemas = scope.protected_schemas(extra=reference.get("protected_schemas"))
print(f"[reconcile] deployer={deployer}")
print(f"[reconcile] protected principals={sorted(protected_principals)}")
print(f"[reconcile] protected schemas={sorted(protected_schemas)}")

# COMMAND ----------

# DBTITLE 1,Diff in Spark -> per-dimension delta (ADD / REMOVE / CHANGE)
from framework.reconcile import diff

delta_frames = diff.diff_all(target, live_frames,
                             target_catalog=target_catalog,
                             protected_principals=protected_principals,
                             protected_schemas=protected_schemas)
delta_counts = {}
for dim, df in delta_frames.items():
    rows = df.collect()
    delta_counts[dim] = [r.asDict() for r in rows]  # dicts: the shape apply/summary consume
    print(f"=== delta: {dim} ({len(rows)} change(s)) ===")
    show(rows)

# COMMAND ----------

# DBTITLE 1,Preflight -> warnings (referenced objects/functions that don't exist)
from framework.reconcile import preflight

warnings = preflight.check_existence(state.refs, existing)
warn_df = frames.warnings_frame(spark, warnings)  # noqa: F821
print(f"=== warnings: {len(warnings)} ===")
show(warn_df)

# COMMAND ----------

# DBTITLE 1,Dynamic views — serving schema + CREATE OR REPLACE (RLS semi-join / pass-through)
# rbac_views.yaml lists the dynamic views the matrix expects (base table -> serving schema). We
# DETECT missing serving schemas + views to create/drop, PREVIEW the DDL, and — in mode=apply —
# CREATE OR REPLACE every expected view (RLS semi-join when rls_column is set, else pass-through)
# and DROP live views in the serving schemas that are not in the matrix. Idempotent (ensure-all).
#
# FUTURE WORK: this orchestration (live-view read + ensure/drop loop) lives in the notebook, unlike
# grants/tags/policies which go through statement.plan_all + executor.apply. Move it into
# framework/ (live.read_live_views + a plan_views planner run via executor) for consistency and
# unit-testability. Deferred by decision — ensure-all keeps it correct; this is a tidiness refactor.
# Per-view drift *detection* (a stored (source, rls_column) contract / applied-state ledger) is also
# deferred: ensure-all re-asserts every view each apply, so live always converges to desired.
from framework.apply import statement as _stmt

view_specs = {v["view"]: v for v in (docs.get("rbac_views") or {}).get("views", [])}
expected_views = sorted(view_specs)
serving_schemas = sorted({".".join(v.split(".")[:2]) for v in expected_views})
mode = dbutils.widgets.get("mode").strip()  # noqa: F821  (also read in the apply cell)

# RLS allow-list (group -> rls_column) the semi-join filters on; from config/governance/<env>.yaml.
rls_entitlement_table = governance.get("rls_entitlement_table")
rls_group_column = governance.get("rls_group_column", "ad_group")

existing_schemas, live_views = set(), set()
for _cat in sorted({s.split(".")[0] for s in serving_schemas}):
    try:
        for r in spark.sql(f"SELECT schema_name FROM `{_cat}`.information_schema.schemata").collect():  # noqa: F821
            existing_schemas.add(f"{_cat}.{r['schema_name']}")
        for r in spark.sql(f"SELECT table_schema, table_name FROM `{_cat}`.information_schema.views").collect():  # noqa: F821
            live_views.add(f"{_cat}.{r['table_schema']}.{r['table_name']}")
    except Exception as exc:  # missing catalog / no access — report and continue
        print(f"[views] could not read information_schema in {_cat}: {exc}")

serving_live = {v for v in live_views if ".".join(v.split(".")[:2]) in serving_schemas}
views_to_drop = sorted(serving_live - set(expected_views))
print(f"[views] serving schemas expected: {serving_schemas}")
print(f"[views] views to CREATE now ({len(set(expected_views) - live_views)}): "
      f"{sorted(set(expected_views) - live_views)}")
if views_to_drop:
    print(f"[views] live views not in the matrix -> DROP ({len(views_to_drop)}): {views_to_drop}")

# Build the DDL (pure); preview in both modes, execute only in apply.
view_ddl = []
for v in expected_views:
    spec = view_specs[v]
    if spec.get("rls_column") and not rls_entitlement_table:
        print(f"[views] WARNING {v}: rls_column set but governance.rls_entitlement_table missing — skipped")
        continue
    view_ddl.append((v, _stmt.view_statement(spec, entitlement_table=rls_entitlement_table,
                                             group_col=rls_group_column)))

print(f"\n[views] {len(view_ddl)} view DDL ({'EXECUTE' if mode == 'apply' else 'PREVIEW — plan mode'}):")
for v, sql in view_ddl:
    print(f"\n-- {v}\n{sql}")

if mode == "apply":
    for s in serving_schemas:                                    # ensure serving schema exists
        _c, _s = s.split(".", 1)
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{_c}`.`{_s}`")   # noqa: F821
    for _v, sql in view_ddl:
        spark.sql(sql)                                           # noqa: F821  (CREATE OR REPLACE VIEW)
    for _v in views_to_drop:
        spark.sql(_stmt.drop_view_statement(_v))                 # noqa: F821
    print(f"\n[views] APPLIED — {len(view_ddl)} view(s) ensured, {len(views_to_drop)} dropped")
else:
    print("\n[views] plan mode — nothing created/dropped")

# COMMAND ----------

# DBTITLE 1,Apply -> execute the grant + tag + policy delta (GRANT/REVOKE / SET TAGS / CREATE OR REPLACE|DROP POLICY)
# mode=plan (default) previews the SQL and runs nothing; mode=apply executes via spark.sql.
# Column-mask policies are applied as ABAC DDL -> requires ABAC-capable compute.
from framework.apply import statement, executor
from dataclasses import asdict

mode = dbutils.widgets.get("mode").strip()
dry_run = mode != "apply"

# Policy CREATE OR REPLACE needs the full compiled definition (MATCH COLUMNS / fn / EXCEPT),
# which the delta row alone doesn't carry — look it up by (scope, policy_name).
policy_defs = {(p.scope, p.policy_name): asdict(p) for p in state.policies}

planned = statement.plan_all(delta_counts, policy_defs=policy_defs)  # [(dimension, row, sql)]
apply_results = executor.apply(spark, planned, dry_run=dry_run)  # noqa: F821
apply_res_df = executor.results_frame(spark, apply_results)      # noqa: F821

from collections import Counter as _Counter
status_counts = dict(_Counter(r["status"] for r in apply_results))
print(f"[apply] mode={mode} ({'DRY-RUN — nothing executed' if dry_run else 'EXECUTED'}) | "
      f"{len(planned)} statement(s) | {status_counts}")
print("[apply] note: column-mask policy DDL requires ABAC-capable compute")
show(apply_res_df)

# COMMAND ----------

# DBTITLE 1,Summary
from collections import Counter

parts = []
for dim, rows in delta_counts.items():
    c = Counter(r["change_type"] for r in rows)
    parts.append(f"{dim} {dict(c)}" if rows else f"{dim} up-to-date")
print(f"RECONCILE OK | catalog={target_catalog} | " + " | ".join(parts)
      + f" | warnings={len(warnings)} | ignored_outside_catalog={len(outside)}")
print(f"APPLY {mode.upper()} | {len(planned)} statement(s) | {status_counts}")
