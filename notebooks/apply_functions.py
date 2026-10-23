# Databricks notebook source
# MAGIC %md
# MAGIC # Apply SQL objects — `CREATE OR REPLACE` the env's mask UDFs + `group_portfolio_access` MV
# MAGIC
# MAGIC The SQL templates are **deployment content, not part of the wheel**: the `*.sql` files in the
# MAGIC `sql_path` folder (the configs repo's `sql/<env>/`, deployed by the platform pipeline). They
# MAGIC contain no deployment specifics: `${catalog}` / `${governance_schema}` / `${unmasked_condition}`
# MAGIC are filled in by the wheel's renderer from the env's reference YAML (`target_catalog`,
# MAGIC `governance_schemas`, `groups`). Every object is applied **idempotently** (`CREATE OR REPLACE`),
# MAGIC so re-running is a no-op.
# MAGIC
# MAGIC The target governance schema must already exist (`ucgov-provision schemas`, the platform pipeline).
# MAGIC The `group_portfolio_access` MV needs **serverless** compute.
# MAGIC
# MAGIC **Widgets:** `reference_path` (required — the env's reference YAML, e.g.
# MAGIC `.../reference/esg.yaml`), `sql_path` (required — the env's SQL folder, e.g. `.../sql/esg`);
# MAGIC empty / missing / unreadable stops the notebook. `mode` = `plan` (default; prints the SQL,
# MAGIC runs nothing) | `apply`.
# MAGIC
# MAGIC **RLS allow-list check:** after applying, the groups in `rls_entitlement_table` are compared
# MAGIC with the reference's `groups`. A group that isn't there (e.g. renamed, with entitlement rows
# MAGIC still on the old name) grants nothing — its members silently see no RLS rows — so it is
# MAGIC reported as a WARNING.
# MAGIC
# MAGIC **Package:** `eastspring-uc-governance` comes from the **job's environment** (the wheel is a
# MAGIC dependency there, set by the deploy pipeline), so the notebook installs nothing itself. To run it
# MAGIC by hand, add the deployed wheel as a dependency in the notebook's **Environment** panel.

# COMMAND ----------

dbutils.widgets.text("reference_path", "", "reference allow-list YAML (required)")
dbutils.widgets.text("sql_path", "", "SQL folder, e.g. .../sql/esg (required)")
dbutils.widgets.dropdown("mode", "plan", ["plan", "apply"])

# COMMAND ----------

# DBTITLE 1,Load the env reference + render the env's SQL folder
from eastspring_uc_governance.sql_objects import render_sql_objects
from eastspring_uc_governance.validation import load_reference

reference_path = dbutils.widgets.get("reference_path").strip()   # required — never derived
sql_path = dbutils.widgets.get("sql_path").strip()               # required — never derived
mode = dbutils.widgets.get("mode").strip()
reference, reason = load_reference(reference_path)
if reason:
    raise ValueError(f"[functions] {reason} — set the reference_path widget")
if not sql_path:
    raise ValueError("[functions] SQL folder path is empty — set the sql_path widget")
print(f"[functions] reference={reference_path} | sql={sql_path} | mode={mode}")

objects = render_sql_objects(reference, sql_path)   # raises on a bad folder / missing value / unknown placeholder
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

# COMMAND ----------

# DBTITLE 1,RLS allow-list: every group in it must be a reference group
from eastspring_uc_governance.sql_objects import unknown_allow_list_groups


def _bt(fqn):
    return ".".join(f"`{seg.strip('`')}`" for seg in fqn.split("."))


rls_table = str(reference.get("rls_entitlement_table") or "").strip()
rls_group_column = reference.get("rls_group_column", "ad_group")
if not rls_table:
    print("[functions] no rls_entitlement_table in the reference — RLS allow-list not checked")
else:
    try:
        allow_list_groups = [r[0] for r in spark.sql(                       # noqa: F821
            f"SELECT DISTINCT {_bt(rls_group_column)} FROM {_bt(rls_table)}").collect()]
    except Exception as exc:
        allow_list_groups = None
        print(f"[functions] RLS allow-list {rls_table} not readable "
              f"({type(exc).__name__}: {str(exc).splitlines()[0][:200]})"
              + (" — it is created in apply mode" if mode != "apply" else ""))
    if allow_list_groups is not None:
        unknown = unknown_allow_list_groups(allow_list_groups, reference)
        print(f"[functions] RLS allow-list {rls_table}: {len(allow_list_groups)} group(s), "
              f"{len(unknown)} not in the reference")
        for g in unknown:
            print(f"[functions] WARNING RLS allow-list group {g!r} is not in the reference's groups — "
                  f"its rows grant nothing (renamed group? fix the entitlement rows)")

if any(s.startswith("FAILED") for _, s in results):
    raise ValueError("one or more SQL objects failed — see above")
