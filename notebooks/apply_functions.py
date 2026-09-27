# Databricks notebook source
# MAGIC %md
# MAGIC # Apply UDFs — `CREATE OR REPLACE` every function defined in `sql/`
# MAGIC
# MAGIC Git is the source of truth for UDF bodies (the `acm_mask_*` column-mask functions et al).
# MAGIC CI/CD triggers this notebook **only when files under `sql/**` change** (path-filtered), and
# MAGIC it applies every `sql/*.sql` **idempotently** (`CREATE OR REPLACE`), so re-running is a no-op.
# MAGIC
# MAGIC **Convention:** one statement per file (each file is executed as a single `spark.sql` call —
# MAGIC no `;`-splitting). The target `governance` schema must already exist (seed / Terraform).
# MAGIC
# MAGIC **`mode`:** `plan` (default) prints the SQL and runs nothing; `apply` executes it.

# COMMAND ----------

dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"])

import sys
from pathlib import Path

# Repo root = parent of notebooks/. Contains framework/, config/, sql/ as siblings.
NOTEBOOK_DIR = Path("/Workspace") / Path(
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
).parent.relative_to("/")
REPO_ROOT = NOTEBOOK_DIR.parent
SQL_DIR = REPO_ROOT / "sql"
mode = dbutils.widgets.get("mode").strip()
print(f"[functions] repo_root={REPO_ROOT} | sql_dir={SQL_DIR} | mode={mode}")

# COMMAND ----------

# DBTITLE 1,Apply each sql/*.sql (CREATE OR REPLACE — idempotent)
def show(df):
    try:
        display(df)  # noqa: F821  (Databricks builtin)
    except Exception:
        df.show(truncate=False)


sql_files = sorted(SQL_DIR.glob("*.sql"))
print(f"[functions] {len(sql_files)} SQL file(s): {[f.name for f in sql_files]}")

results = []
for f in sql_files:
    raw = f.read_text().strip()
    stmt = raw[:-1].strip() if raw.endswith(";") else raw   # spark.sql wants a single statement, no trailing ';'
    print(f"\n=== {f.name} ===\n{stmt}")
    if mode != "apply":
        results.append((f.name, "PLANNED"))
        continue
    try:
        spark.sql(stmt)                                     # noqa: F821
        results.append((f.name, "OK"))
    except Exception as exc:                                # surface, keep going, fail at the end
        results.append((f.name, f"FAILED: {exc}"))
        print(f"[functions] ERROR in {f.name}: {exc}")

# COMMAND ----------

# DBTITLE 1,Summary
from collections import Counter

status = Counter(s.split(":", 1)[0] for _, s in results)
print(f"[functions] mode={mode} ({'DRY-RUN' if mode != 'apply' else 'EXECUTED'}) | "
      f"{len(results)} file(s) | {dict(status)}")
show(spark.createDataFrame(results or [("<none>", "")], ["sql_file", "status"]))  # noqa: F821

if any(s.startswith("FAILED") for _, s in results):
    raise ValueError("one or more function statements failed — see above")
