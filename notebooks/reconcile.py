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
# MAGIC **`reference_path`** (required — never derived from `config_path`): the reference allow-list
# MAGIC YAML, e.g. `.../reference/esg.yaml`. Hand-curated per-deployment source of truth for
# MAGIC the **`target_catalog`**, the
# MAGIC **protected set** (`protected_principals` = `deployers` + `admin_groups`, plus
# MAGIC `protected_schemas`), and the approved vocabulary (groups / schemas / governed_tags)
# MAGIC that off-list use warns against. Empty, missing or unreadable -> the notebook stops before
# MAGIC anything runs (without it full-sync would have no protected set).
# MAGIC
# MAGIC **`mode`:** `plan` (default) previews the SQL and changes nothing; `apply`
# MAGIC executes the grant + tag + column-mask policy statements (`CREATE OR REPLACE`/`DROP
# MAGIC POLICY` — requires **ABAC-capable compute**) and the dynamic views.
# MAGIC
# MAGIC **Apply order — tighten first, loosen last** (`statement.PHASES`), so data is never
# MAGIC readable unmasked while it changes: **masking** (policies, then tags) -> **views** -> **access**
# MAGIC (grants, ADD before REMOVE) -> **loosen** (UNSET tags, DROP policies). If a masking statement
# MAGIC fails, nothing after it runs (SKIPPED). Any FAILED / SKIPPED statement fails the notebook.
# MAGIC
# MAGIC **Package:** `eastspring-uc-governance` comes from the **job's environment** (the wheel is a
# MAGIC dependency there, set by the deploy pipeline), so the notebook installs nothing itself. To run it
# MAGIC by hand, add the deployed wheel as a dependency in the notebook's **Environment** panel.

# COMMAND ----------

dbutils.widgets.text("config_path", "", "config folder, e.g. .../config/esg (required)")
dbutils.widgets.text("reference_path", "", "reference allow-list YAML (required)")
dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"])

# COMMAND ----------

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

_config_path = dbutils.widgets.get("config_path").strip()   # required — never defaulted
if not _config_path:
    raise ValueError("[reconcile] config folder path is empty — set the config_path widget")
config_path = Path(_config_path)
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

# Reference allow-list: authoritative, hand-curated per-deployment config, NOT generated from the
# workbook. Holds the approved vocabulary (warning layer) + the target catalog, full-sync protected
# set, skip_base_grants, the mask-exempt (ETL) principals, and the RLS entitlement table. Required,
# from the reference_path widget only — never derived from config_path.
from eastspring_uc_governance.validation import load_reference

reference_path = dbutils.widgets.get("reference_path").strip()
reference, reason = load_reference(reference_path)
if reason:
    raise ValueError(f"[reconcile] {reason} — set the reference_path widget")

# ETL/pipeline identities added to EXCEPT on every column-mask policy so they read unmasked data.
mask_exempt_principals = reference.get("mask_exempt_principals") or []
print(f"[reconcile] config_source={config_path} | "
      f"rbac_view_grants={'yes' if 'rbac_view_grants' in docs else 'no'} | "
      f"rbac_views={'yes' if 'rbac_views' in docs else 'no'} | "
      f"reference={reference_path} | "
      f"mask_exempt_principals={mask_exempt_principals}")

# COMMAND ----------

# DBTITLE 1,Validate the config (fail-closed errors; fail-open reference warnings)
from eastspring_uc_governance.validation import validate_docs

# Passing the reference adds fail-open warnings for any group / catalog.schema / tag key+value
# the config uses but the allow-list does not list. Warnings are reported, never block.
vres = validate_docs(docs, reference=reference)
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
from eastspring_uc_governance.compile import compile_config

# mask_exempt_principals is stamped onto each column-mask policy's EXCEPT clause (target state),
# so an exempt-list change surfaces as a policy CHANGE in the diff.
# skip_base_grants (reference): base_grants.yaml is not compiled; grant full-sync is narrowed to the
# serving schemas in the diff cell, so the unmanaged base-table grants are left exactly as they are.
skip_base_grants = reference.get("skip_base_grants") is True   # only a real `true` skips
print(f"[reconcile] skip_base_grants={skip_base_grants}")
state = compile_config(docs, mask_exempt_principals=mask_exempt_principals,
                       include_base_grants=not skip_base_grants)
if not state.ok:
    for err in state.errors:
        print(f"  {err}")
    raise ValueError(f"compile failed: {len(state.errors)} error(s)")

print(f"[reconcile] target: {len(state.grants)} grants | {len(state.tags)} tags | "
      f"{len(state.policies)} policies | {len(state.refs)} referenced objects")

# COMMAND ----------

# DBTITLE 1,Target catalog + target-state DataFrames
from eastspring_uc_governance.reconcile import frames, scope

# Target catalog: the reference's `target_catalog`, and only that (no override) — the catalog a
# run full-syncs is always the one its reference declares.
target_catalog = str(reference.get("target_catalog") or "").strip()
if not target_catalog:
    raise ValueError(f"no target_catalog in the reference ({reference_path})")
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
from eastspring_uc_governance.reconcile import live

# Live column-mask policies come from abac_policy_definitions (Public Preview). Without it they are
# unknown: plan warns (every policy shows as ADD, none as DROP); apply stops here, before any change.
mode = dbutils.widgets.get("mode").strip()                                   # noqa: F821
policies_unavailable = live.abac_policies_unavailable(spark)                 # noqa: F821
if policies_unavailable:
    if mode == "apply":
        raise ValueError(f"[policies] {policies_unavailable} — refusing to apply: the live column-mask "
                         f"policies are unknown, so one removed from the config could never be dropped")
    print(f"[policies] WARNING {policies_unavailable} — this plan shows every policy as ADD and cannot "
          f"show a DROP; an apply would stop here")

live_frames = {
    "grants": live.read_live_grants(spark, catalogs),
    "tags": live.read_live_tags(spark, catalogs),
    "policies": (live.empty_policies(spark) if policies_unavailable
                 else live.read_live_policies(spark, catalogs)),
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
from eastspring_uc_governance.reconcile import diff

# skip_base_grants -> grants full-sync only inside the serving schemas (catalog-level add-only);
# None -> the whole target catalog, as before. Tags / policies are unaffected.
managed_grant_schemas = scope.managed_grant_schemas(reference, target_catalog)
print(f"[reconcile] grant full-sync scope="
      f"{'whole catalog' if managed_grant_schemas is None else managed_grant_schemas}")
delta_frames = diff.diff_all(target, live_frames,
                             target_catalog=target_catalog,
                             protected_principals=protected_principals,
                             protected_schemas=protected_schemas,
                             managed_grant_schemas=managed_grant_schemas)
delta_counts = {}
for dim, df in delta_frames.items():
    rows = df.collect()
    delta_counts[dim] = [r.asDict() for r in rows]  # dicts: the shape apply/summary consume
    print(f"=== delta: {dim} ({len(rows)} change(s)) ===")
    show(rows)

# COMMAND ----------

# DBTITLE 1,Preflight -> warnings (referenced objects/functions that don't exist)
from eastspring_uc_governance.reconcile import preflight

warnings = preflight.check_existence(state.refs, existing)
warn_df = frames.warnings_frame(spark, warnings)  # noqa: F821
print(f"=== warnings: {len(warnings)} ===")
show(warn_df)

# COMMAND ----------

# DBTITLE 1,Dynamic views — CREATE OR REPLACE (RLS semi-join / pass-through) in the provisioned serving schemas
# rbac_views.yaml lists the dynamic views the matrix expects (base table -> serving schema). We
# DETECT views to create/drop and PREVIEW the DDL. Nothing runs here: the apply cell runs these
# statements AFTER the masking phase — a new view is readable at once through the serving
# schema's schema-level grant, so its columns' masks must already exist. In mode=apply that
# CREATE OR REPLACEs every expected view (RLS semi-join when rls_column is set, else
# pass-through) and DROPs live views in the serving schemas that are not in the matrix.
# Idempotent (ensure-all). Schemas are NOT created here: they come from the reference via
# ucgov-provision schemas (platform pipeline); a missing one stops an apply before any change.
#
# FUTURE WORK: the live-view read + ensure/drop list is built here in the notebook, unlike
# grants/tags/policies which come from statement.plan_phase. Move it into the
# eastspring_uc_governance package (live.read_live_views + a plan_views planner) for consistency
# and unit-testability. Deferred by decision — ensure-all keeps it correct; this is a tidiness refactor.
# Per-view drift *detection* (a stored (source, rls_column) contract / applied-state ledger) is also
# deferred: ensure-all re-asserts every view each apply, so live always converges to desired.
from eastspring_uc_governance.apply import statement as _stmt

view_specs = {v["view"]: v for v in (docs.get("rbac_views") or {}).get("views", [])}
expected_views = sorted(view_specs)
serving_schemas = sorted({".".join(v.split(".")[:2]) for v in expected_views})
mode = dbutils.widgets.get("mode").strip()  # noqa: F821  (also read in the apply cell)

# RLS allow-list (group -> rls_column) the semi-join filters on; from the reference (reference_path).
rls_entitlement_table = reference.get("rls_entitlement_table")
rls_group_column = reference.get("rls_group_column", "ad_group")

# Preflight: the allow-list must exist with the group column + every rls_column the views use —
# otherwise every RLS view fails. Plan: warning; apply: stop here, before anything is applied.
rls_columns = {spec["rls_column"] for spec in view_specs.values() if spec.get("rls_column")}
rls_live_columns = None
if rls_entitlement_table and rls_columns:
    _parts = [p.strip("`").replace("'", "''") for p in str(rls_entitlement_table).split(".")]
    if len(_parts) == 3:
        rls_live_columns = {r["column_name"] for r in spark.sql(           # noqa: F821
            "SELECT column_name FROM system.information_schema.columns "
            f"WHERE table_catalog = '{_parts[0]}' AND table_schema = '{_parts[1]}' "
            f"AND table_name = '{_parts[2]}'").collect()} or None
rls_problems = preflight.check_rls_table(rls_entitlement_table, rls_live_columns,
                                         group_column=rls_group_column, rls_columns=rls_columns)
print(f"[views] RLS allow-list {rls_entitlement_table or '-'} for rls_column(s) {sorted(rls_columns)}: "
      f"{'OK' if not rls_problems else f'{len(rls_problems)} problem(s)'}")
for _w in rls_problems:
    print(f"[views] WARNING {_w['kind']}: {_w['fqn'] or '-'} — {_w['reason']}")
warnings.extend(rls_problems)
if rls_problems and mode == "apply":
    raise ValueError(f"[views] RLS allow-list not usable ({len(rls_problems)} problem(s) above) — "
                     f"nothing applied")

existing_schemas, live_views = set(), set()
for _cat in sorted({s.split(".")[0] for s in serving_schemas}):
    try:
        for r in spark.sql(f"SELECT schema_name FROM `{_cat}`.information_schema.schemata").collect():  # noqa: F821
            existing_schemas.add(f"{_cat}.{r['schema_name']}")
        for r in spark.sql(f"SELECT table_schema, table_name FROM `{_cat}`.information_schema.views").collect():  # noqa: F821
            live_views.add(f"{_cat}.{r['table_schema']}.{r['table_name']}")
    except Exception as exc:  # missing catalog / no access — report and continue
        print(f"[views] could not read information_schema in {_cat}: {exc}")

# Preflight: every serving schema must already exist (provisioned, never created by reconcile).
schema_problems = preflight.check_view_schemas(serving_schemas, existing_schemas)
print(f"[views] serving schemas expected: {serving_schemas} — "
      f"{'all exist' if not schema_problems else f'{len(schema_problems)} missing'}")
for _w in schema_problems:
    print(f"[views] WARNING {_w['kind']}: {_w['fqn']} — {_w['reason']}")
warnings.extend(schema_problems)
if schema_problems and mode == "apply":
    raise ValueError(f"[views] {len(schema_problems)} serving schema(s) missing: "
                     f"{[w['fqn'] for w in schema_problems]} — run the platform pipeline "
                     f"(ucgov-provision schemas) first; nothing applied")

serving_live = {v for v in live_views if ".".join(v.split(".")[:2]) in serving_schemas}
views_to_drop = sorted(serving_live - set(expected_views))
print(f"[views] views to CREATE now ({len(set(expected_views) - live_views)}): "
      f"{sorted(set(expected_views) - live_views)}")
if views_to_drop:
    print(f"[views] live views not in the matrix -> DROP ({len(views_to_drop)}): {views_to_drop}")

# Build the DDL (pure); preview in both modes, execute only in apply.
view_ddl = []
for v in expected_views:
    spec = view_specs[v]
    if spec.get("rls_column") and not rls_entitlement_table:
        print(f"[views] WARNING {v}: rls_column set but reference.rls_entitlement_table missing — skipped")
        continue
    view_ddl.append((v, _stmt.view_statement(spec, entitlement_table=rls_entitlement_table,
                                             group_col=rls_group_column)))

print(f"\n[views] {len(view_ddl)} view DDL ({'run by the apply cell' if mode == 'apply' else 'PREVIEW — plan mode'}):")
for v, sql in view_ddl:
    print(f"\n-- {v}\n{sql}")

# (dimension, row, sql) triples for the apply cell's "views" phase (between masking and access)
view_planned = (
    [("views", {"change_type": "ENSURE", "securable_fqn": v}, sql) for v, sql in view_ddl]
    + [("views", {"change_type": "REMOVE", "securable_fqn": v}, _stmt.drop_view_statement(v))
       for v in views_to_drop])
print(f"\n[views] {len(view_planned)} statement(s) — run by the apply cell, after the masking phase")

# COMMAND ----------

# DBTITLE 1,Apply -> masking (policies, tags) -> views -> access (grants) -> loosen (UNSET tags, DROP policies)
# mode=plan (default) previews the SQL in run order and runs nothing; mode=apply executes via
# spark.sql. Column-mask policies are applied as ABAC DDL -> requires ABAC-capable compute.
# Tighten first, loosen last (statement.PHASES); a failure in masking SKIPs everything after it.
from eastspring_uc_governance.apply import statement, executor
from dataclasses import asdict

mode = dbutils.widgets.get("mode").strip()
dry_run = mode != "apply"

# Policy CREATE OR REPLACE needs the full compiled definition (MATCH COLUMNS / fn / EXCEPT),
# which the delta row alone doesn't carry — look it up by (scope, policy_name).
policy_defs = {(p.scope, p.policy_name): asdict(p) for p in state.policies}

phases = [                                                       # [(phase, [(dimension, row, sql)])]
    ("masking", statement.plan_phase("masking", delta_counts, policy_defs=policy_defs)),
    ("views", view_planned),
    ("access", statement.plan_phase("access", delta_counts, policy_defs=policy_defs)),
    ("loosen", statement.plan_phase("loosen", delta_counts, policy_defs=policy_defs)),
]
planned = [t for _phase, stmts in phases for t in stmts]
apply_results = executor.apply_phases(spark, phases, dry_run=dry_run)  # noqa: F821
apply_res_df = executor.results_frame(spark, apply_results)            # noqa: F821

from collections import Counter as _Counter
status_counts = dict(_Counter(r["status"] for r in apply_results))
for _phase, _stmts in phases:
    _c = dict(_Counter(r["status"] for r in apply_results if r["phase"] == _phase))
    print(f"[apply] {_phase:<8} {len(_stmts):>4} statement(s) {_c or ''}")
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

# a FAILED (or SKIPPED, after a masking failure) statement fails the run — so the job, and the
# pipeline step waiting on it, report it instead of finishing green
if status_counts.get("FAILED") or status_counts.get("SKIPPED"):
    raise ValueError(f"apply: {status_counts.get('FAILED', 0)} FAILED, "
                     f"{status_counts.get('SKIPPED', 0)} SKIPPED — see the apply results above")
