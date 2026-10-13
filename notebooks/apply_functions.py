# Databricks notebook source
# MAGIC %md
# MAGIC # Apply SQL objects — `CREATE OR REPLACE` the packaged mask UDFs + `group_portfolio_access` MV
# MAGIC
# MAGIC The SQL bodies ship **inside the `eastspring-uc-governance` wheel** (`sql/*.sql`) and contain no
# MAGIC deployment specifics: `${catalog}` / `${governance_schema}` are filled in from the env's
# MAGIC `config/reference/<env>.yaml` (`target_catalog`, `governance_schemas`). Every object is applied
# MAGIC **idempotently** (`CREATE OR REPLACE`), so re-running is a no-op.
# MAGIC
# MAGIC The target governance schema must already exist (`ucgov-provision schemas`, or seed / Terraform).
# MAGIC The `group_portfolio_access` MV needs **serverless** compute.
# MAGIC
# MAGIC **Widgets:** `wheel_path` (the wheel on a UC Volume), `config_path` (the env's config dir —
# MAGIC its sibling `reference/<env>.yaml` is read), `mode` = `plan` (default; prints the SQL, runs
# MAGIC nothing) | `apply`.

# COMMAND ----------

dbutils.widgets.text("wheel_path",
                     "/Volumes/hera-dev/governance/wheels/eastspring_uc_governance-0.1.0-py3-none-any.whl")
dbutils.widgets.text("config_path", "/Workspace/Users/xieming.li@databricks.com/eastspring-governance/config/esg")
dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"])

wheel_path = dbutils.widgets.get("wheel_path").strip()

# COMMAND ----------

# MAGIC %pip install "$wheel_path"

# COMMAND ----------

dbutils.library.restartPython()   # clears Python state — widgets are re-read below

# COMMAND ----------

# DBTITLE 1,Load the env reference + render the packaged SQL
from pathlib import Path

import yaml

from eastspring_uc_governance.sql_objects import render_sql_objects

config_path = Path(dbutils.widgets.get("config_path").strip())
mode = dbutils.widgets.get("mode").strip()
reference_path = config_path.parent / "reference" / f"{config_path.name}.yaml"
reference = yaml.safe_load(reference_path.read_text()) or {}
print(f"[functions] reference={reference_path} | mode={mode}")

objects = render_sql_objects(reference)   # raises on a missing value / unknown placeholder
print(f"[functions] {len(objects)} SQL object(s): {[name for name, _ in objects]}")

# COMMAND ----------

# DBTITLE 1,Apply each object (CREATE OR REPLACE — idempotent)
def show(df):
    try:
        display(df)  # noqa: F821  (Databricks builtin)
    except Exception:
        df.show(truncate=False)


results = []
for name, stmt in objects:
    print(f"\n=== {name} ===\n{stmt}")
    if mode != "apply":
        results.append((name, "PLANNED"))
        continue
    try:
        spark.sql(stmt)                                     # noqa: F821
        results.append((name, "OK"))
    except Exception as exc:                                # surface, keep going, fail at the end
        results.append((name, f"FAILED: {exc}"))
        print(f"[functions] ERROR in {name}: {exc}")

# COMMAND ----------

# DBTITLE 1,Summary
from collections import Counter

status = Counter(s.split(":", 1)[0] for _, s in results)
print(f"[functions] mode={mode} ({'DRY-RUN' if mode != 'apply' else 'EXECUTED'}) | "
      f"{len(results)} object(s) | {dict(status)}")
show(spark.createDataFrame(results or [("<none>", "")], ["sql_object", "status"]))  # noqa: F821

if any(s.startswith("FAILED") for _, s in results):
    raise ValueError("one or more SQL objects failed — see above")
