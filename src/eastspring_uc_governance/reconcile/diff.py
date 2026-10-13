"""Per-dimension diff of target vs. live state, using the native PySpark DataFrame API.

Under full-sync the live grant set is the whole catalog (potentially millions of rows
across tables x principals), so the diff runs distributed — a ``full_outer`` join per
dimension — and never collects live rows to the driver. Output is one delta DataFrame
per dimension with a ``change_type`` column (ADD / REMOVE / CHANGE).

Scope rules are applied as DataFrame filters before the join:

  * grants  — full-sync within ``target_catalog``, minus protected principals/schemas; with
              ``skip_base_grants``, only within the serving schemas (catalog-level add-only).
  * tags    — ``acm_*`` keys only, minus protected schemas.
  * policies— ``acm_policy_*`` names only, minus protected schemas.

Target rows outside ``target_catalog`` are dropped (ignored).
"""
from __future__ import annotations
import operator
from functools import reduce
from typing import Iterable
from pyspark.sql import DataFrame, functions as F

GRANT_KEYS = ["principal", "privilege_type", "securable_type", "securable_fqn"]
TAG_KEYS = ["securable_type", "securable_fqn", "tag_key"]
POLICY_KEYS = ["scope", "policy_name"]


# ---- fqn segment columns ------------------------------------------------

def _segment(fqn_col: str, idx: int) -> Column:
    """1-based segment of a dotted fqn, or NULL if absent (try_element_at never throws)."""
    return F.try_element_at(F.split(F.col(fqn_col), r"\."), F.lit(idx))


# ---- scope filters (DataFrame -> DataFrame) -----------------------------

def _scope(df: DataFrame, *, fqn_col: str, target_catalog: str | None = None,
           protected_schemas: Iterable[str] = (), principal_col: str | None = None,
           protected_principals: Iterable[str] = (), acm_col: str | None = None,
           acm_prefix: str | None = None) -> DataFrame:
    if target_catalog:
        df = df.filter(_segment(fqn_col, 1) == target_catalog)

    schemas = [s for s in protected_schemas if s]
    if schemas:
        sch = _segment(fqn_col, 2)
        df = df.filter(sch.isNull() | ~sch.isin(*schemas))  # catalog-level (NULL) is exempt

    principals = [p for p in protected_principals if p]
    if principal_col and principals:
        df = df.filter(~F.col(principal_col).isin(*principals))

    if acm_col and acm_prefix:
        df = df.filter(F.col(acm_col).startswith(acm_prefix))

    return df


# ---- generic diff (full outer join) -------------------------------------

def _diff(target: DataFrame, live: DataFrame,
          key_cols: list[str], value_cols: list[str]) -> DataFrame:
    """full_outer join target vs live -> delta rows with change_type.

    ADD = target only; REMOVE = live only; CHANGE = same key, a value col differs.
    Output columns coalesce target-first, so ADD/CHANGE carry the desired row and
    REMOVE the live row (matching the earlier semantics)."""
    t, l = target.alias("t"), live.alias("l")
    on = reduce(operator.and_, (F.col(f"t.{k}") == F.col(f"l.{k}") for k in key_cols))
    joined = t.join(l, on, "full_outer")

    fk = key_cols[0]
    added = F.col(f"l.{fk}").isNull()      # live row absent   -> ADD
    removed = F.col(f"t.{fk}").isNull()    # target row absent -> REMOVE
    changed = reduce(                      # any value col differs -> CHANGE (False if none)
        operator.or_,
        (~F.col(f"t.{c}").eqNullSafe(F.col(f"l.{c}")) for c in value_cols),
        F.lit(False),
    )

    change_type = (F.when(added, "ADD")
                   .when(removed, "REMOVE")
                   .when(changed, "CHANGE")
                   .alias("change_type"))

    out = [F.coalesce(F.col(f"t.{c}"), F.col(f"l.{c}")).alias(c) for c in key_cols + value_cols]
    return joined.where(added | removed | changed).select(*out, change_type)


# ---- per-dimension diffs ------------------------------------------------

def diff_grants(target: DataFrame, live: DataFrame, *, target_catalog: str | None = None,
                protected_principals: Iterable[str] = (),
                protected_schemas: Iterable[str] = (),
                managed_schemas: Iterable[str] | None = None) -> DataFrame:
    """Grant delta. ``managed_schemas`` (bare names; None = the whole catalog) narrows full-sync to
    those schemas — used when the deployment skips base grants. Grants in other schemas are then
    ignored on BOTH sides (never added, never revoked), and catalog-level grants become add-only:
    a missing catalog grant is still ADDed, but none is REMOVEd (the catalog isn't ours to clean)."""
    t = _scope(target, fqn_col="securable_fqn", target_catalog=target_catalog,
               protected_schemas=protected_schemas)
    l = _scope(live, fqn_col="securable_fqn", target_catalog=target_catalog,
               protected_schemas=protected_schemas, principal_col="principal",
               protected_principals=protected_principals)
    if managed_schemas is None:
        return _diff(t, l, GRANT_KEYS, value_cols=[])

    schemas = list(managed_schemas)
    sch = _segment("securable_fqn", 2)
    in_scope = sch.isNull() | (sch.isin(*schemas) if schemas else F.lit(False))   # NULL = catalog-level
    delta = _diff(t.filter(in_scope), l.filter(in_scope), GRANT_KEYS, value_cols=[])
    return delta.filter(~((F.col("change_type") == "REMOVE") & sch.isNull()))


def diff_tags(target: DataFrame, live: DataFrame, *, target_catalog: str | None = None,
              protected_schemas: Iterable[str] = ()) -> DataFrame:
    t = _scope(target, fqn_col="securable_fqn", target_catalog=target_catalog,
               protected_schemas=protected_schemas)
    l = _scope(live, fqn_col="securable_fqn", target_catalog=target_catalog,
               protected_schemas=protected_schemas, acm_col="tag_key", acm_prefix="acm_")
    return _diff(t, l, TAG_KEYS, value_cols=["tag_value"])


def diff_policies(target: DataFrame, live: DataFrame, *, target_catalog: str | None = None,
                  protected_schemas: Iterable[str] = ()) -> DataFrame:
    t = _scope(target, fqn_col="scope", target_catalog=target_catalog,
               protected_schemas=protected_schemas)
    l = _scope(live, fqn_col="scope", target_catalog=target_catalog,
               protected_schemas=protected_schemas, acm_col="policy_name", acm_prefix="acm_policy_")
    # CHANGE fires when the mask function OR the EXCEPT (ETL-exempt) list drifts. policy_type is
    # not compared: it's always column_mask now, and live reads it from abac_policy_definitions.
    return _diff(t, l, POLICY_KEYS, value_cols=["function", "except_principals"])


def diff_all(target_frames: dict[str, DataFrame], live_frames: dict[str, DataFrame], *,
             target_catalog: str, protected_principals: Iterable[str] = (),
             protected_schemas: Iterable[str] = (),
             managed_grant_schemas: Iterable[str] | None = None) -> dict[str, DataFrame]:
    """Diff every dimension. ``target_frames`` / ``live_frames`` are dicts keyed
    ``grants`` / ``tags`` / ``policies`` (target from ``frames.target_frames``, live from
    ``live.read_live_*``). ``managed_grant_schemas`` narrows the GRANT dimension only (see
    ``diff_grants``); tags and policies keep the whole-catalog scope."""
    return {
        "grants": diff_grants(target_frames["grants"], live_frames["grants"],
                              target_catalog=target_catalog,
                              protected_principals=protected_principals,
                              protected_schemas=protected_schemas,
                              managed_schemas=managed_grant_schemas),
        "tags": diff_tags(target_frames["tags"], live_frames["tags"],
                          target_catalog=target_catalog, protected_schemas=protected_schemas),
        "policies": diff_policies(target_frames["policies"], live_frames["policies"],
                                  target_catalog=target_catalog, protected_schemas=protected_schemas),
    }
