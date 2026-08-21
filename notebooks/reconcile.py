# Databricks notebook source
# MAGIC %md
# MAGIC # Governance reconcile — target state vs. live Unity Catalog
# MAGIC
# MAGIC Compiles the validated YAML config into target state, reads live state from
# MAGIC `system.information_schema` (+ `SHOW POLICIES`), and emits a **per-dimension
# MAGIC delta** (`change_type` = ADD / REMOVE / CHANGE) plus a **warning list** of
# MAGIC referenced objects/functions that don't exist. It changes nothing.
# MAGIC
# MAGIC **`config_path`:** workspace path holding `grants.yaml` / `tags.yaml` / `policies.yaml`
# MAGIC (default: the uploaded `config/uat`). The notebook reads the three files from there.
# MAGIC Catalogs to reconcile are **computed from the config** (the first segment of each
# MAGIC fully-qualified name) — no catalog parameter needed.

# COMMAND ----------

dbutils.widgets.text("config_path", "/Workspace/Users/xieming.li@databricks.com/eastspring-governance/config/uat")

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

# DBTITLE 1,Load config from the workspace path
from pathlib import Path
import yaml

config_path = Path(dbutils.widgets.get("config_path"))
docs = {
    k: yaml.safe_load((config_path / f"{k}.yaml").read_text())
    for k in ("grants", "tags", "policies")
}
print(f"[reconcile] config_source={config_path}")

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

# DBTITLE 1,Target-state DataFrames
from framework.reconcile import frames, scope

catalogs = scope.catalogs_from_config(docs)   # first segment of every securable FQN in config
print(f"[reconcile] catalogs in scope (from config): {catalogs}")

target = frames.target_frames(spark, state)  # noqa: F821  (spark builtin)
for dim, df in target.items():
    print(f"target {dim}: {df.count()} rows")
    show(df)

# COMMAND ----------

# DBTITLE 1,Read live state from information_schema (+ SHOW POLICIES)
from framework.reconcile import live

live_grants = live.read_live_grants(spark, catalogs)          # noqa: F821
live_tags = live.read_live_tags(spark, catalogs)              # noqa: F821
live_policies = live.read_live_policies(spark, scope.policy_scopes(state))  # noqa: F821
existing = live.read_existing_objects(spark, catalogs)        # noqa: F821
print(f"[reconcile] live: {len(live_grants)} grants | {len(live_tags)} tags | "
      f"{len(live_policies)} policies | {len(existing)} objects")

# COMMAND ----------

# DBTITLE 1,Diff -> per-dimension delta (ADD / REMOVE / CHANGE)
from framework.reconcile import diff

deltas = diff.diff_all(state, live_grants, live_tags, live_policies)
delta_frames = frames.delta_frames(spark, deltas)  # noqa: F821
for dim, df in delta_frames.items():
    print(f"=== delta: {dim} ({df.count()} change(s)) ===")
    show(df)

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
for dim, rows in deltas.items():
    c = Counter(r["change_type"] for r in rows)
    parts.append(f"{dim} {dict(c)}" if rows else f"{dim} up-to-date")
print("RECONCILE OK | " + " | ".join(parts) + f" | warnings={len(warnings)}")
