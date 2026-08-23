# Databricks notebook source
# MAGIC %md
# MAGIC # Governance reconcile — full-sync a target catalog against the YAML config
# MAGIC
# MAGIC Compiles the validated YAML into target state, reads live state from
# MAGIC `system.information_schema` (+ `SHOW POLICIES`), and emits a **per-dimension
# MAGIC delta** (`change_type` = ADD / REMOVE / CHANGE) plus a **warning list** of
# MAGIC referenced objects/functions that don't exist. It changes nothing.
# MAGIC
# MAGIC **v1 full-sync scope:** the **`target_catalog`** parameter is treated as
# MAGIC framework-owned — every live grant in it that the YAML does not declare is a
# MAGIC **REMOVE**, except grants to a **protected principal** (the deploying identity +
# MAGIC configured `admin_groups`) or in a **protected schema** (`information_schema` +
# MAGIC configured extras). Tags/policies stay `acm_`-namespaced. Anything outside the
# MAGIC target catalog is ignored.
# MAGIC
# MAGIC **`config_path`:** workspace path holding `grants.yaml` / `tags.yaml` /
# MAGIC `policies.yaml` and an optional `governance.yaml`.

# COMMAND ----------

dbutils.widgets.text("config_path", "/Workspace/Users/xieming.li@databricks.com/eastspring-governance/config/uat")
dbutils.widgets.text("target_catalog", "asset_prod")

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

# DBTITLE 1,Load config from the workspace path (governance.yaml optional)
from pathlib import Path
import yaml

config_path = Path(dbutils.widgets.get("config_path"))
docs = {
    k: yaml.safe_load((config_path / f"{k}.yaml").read_text())
    for k in ("grants", "tags", "policies")
}
gov_path = config_path / "governance.yaml"
if gov_path.exists():
    docs["governance"] = yaml.safe_load(gov_path.read_text())
print(f"[reconcile] config_source={config_path} | governance={'yes' if 'governance' in docs else 'no'}")

# COMMAND ----------

# DBTITLE 1,Validate the config (fail-closed)
from framework.validation import validate_docs

vres = validate_docs(docs)
if not vres.ok:
    for issue in vres.sorted_issues():
        print(f"  {issue}")
    raise ValueError(f"config invalid: {len(vres.issues)} issue(s) — fix before reconciling")
print("[reconcile] config valid")

# COMMAND ----------

# DBTITLE 1,Compile -> target state (grants / tags / policies + referenced objects)
from framework.compile import compile_config

state = compile_config(docs)
if not state.ok:
    for err in state.errors:
        print(f"  {err}")
    raise ValueError(f"compile failed: {len(state.errors)} error(s)")

print(f"[reconcile] target: {len(state.grants)} grants | {len(state.tags)} tags | "
      f"{len(state.policies)} policies | {len(state.refs)} referenced objects")

# COMMAND ----------

# DBTITLE 1,Target catalog + target-state DataFrames
from framework.reconcile import frames, scope

target_catalog = dbutils.widgets.get("target_catalog").strip()
if not target_catalog:
    raise ValueError("`target_catalog` widget is required")
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

# DBTITLE 1,Read live state from information_schema (+ SHOW POLICIES) — as DataFrames
from framework.reconcile import live

live_frames = {
    "grants": live.read_live_grants(spark, catalogs),                        # noqa: F821
    "tags": live.read_live_tags(spark, catalogs),                            # noqa: F821
    "policies": live.read_live_policies(spark, scope.policy_scopes(state)),  # noqa: F821
}
existing = live.read_existing_objects(spark, catalogs)                       # noqa: F821
print(f"[reconcile] live objects for preflight: {len(existing)}")

# COMMAND ----------

# DBTITLE 1,Protected set (full-sync guardrail — never proposed for REMOVE)
deployer = spark.sql("SELECT current_user()").collect()[0][0]  # noqa: F821
gov = docs.get("governance") or {}
protected_principals = scope.protected_principals(
    deployer=deployer,
    deployers=gov.get("deployers"),
    admin_groups=gov.get("admin_groups"))
protected_schemas = scope.protected_schemas(extra=gov.get("protected_schemas"))
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
    delta_counts[dim] = rows
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

# DBTITLE 1,Summary
from collections import Counter

parts = []
for dim, rows in delta_counts.items():
    c = Counter(r["change_type"] for r in rows)
    parts.append(f"{dim} {dict(c)}" if rows else f"{dim} up-to-date")
print(f"RECONCILE OK | catalog={target_catalog} | " + " | ".join(parts)
      + f" | warnings={len(warnings)} | ignored_outside_catalog={len(outside)}")
