"""Tests for the Access-Matrix generator (pure transform + one real-workbook integration).

v3 dynamic-views layout: Base Tables / Base Grant Rules / Base Grant Exceptions + Rbac Views.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.compile import compile_config
from eastspring_uc_governance.generate import generate_config, read_workbook
from eastspring_uc_governance.generate.generator import GeneratorError, REQUIRED_SHEETS, write_config
from eastspring_uc_governance.validation import validate_dir, validate_docs

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKBOOK = FIXTURES / "access-matrix-esg_workshop-v3-dynamic-views.xlsx"   # frozen copy of the v3 sign-off


def _wb(*, groups=("g_read", "g_admin"), data_products=("dp_a", "dp_b"), tables=None,
        grant_rules=None, exceptions=None, rbac_views=None, asset_classification=None,
        masking_rules=None, bundles=("READ", "WRITE", "OWNER", "ADMIN")):
    """Build a minimal, otherwise-valid v3 workbook (tab -> row-dicts)."""
    if tables is None:
        tables = [
            {"catalog": "c", "schema": "s", "object": "t1", "object_type": "table",
             "data_product": "dp_a", "target_schema": "c.dm_x", "rls_column": "portfolio_code"},
            {"catalog": "c", "schema": "s", "object": "t2", "object_type": "table",
             "data_product": "dp_b", "target_schema": "c.dm_x"},
        ]
    if grant_rules is None:
        grant_rules = [{"rule_id": "GR1", "group_name": "g_admin", "match_label_key": "data_product",
                        "match_label_value": "dp_a", "bundle": "ADMIN", "object_types": "table; view"}]
    if rbac_views is None:
        rbac_views = [{"rule_id": "RBAC1", "group_name": "g_read",
                       "catalog": "c", "schema": "dm_x", "bundle": "READ"}]
    return {
        "Groups": [{"group_name": g} for g in groups],
        "Data Products": [{"data_product": d} for d in data_products],
        "Base Tables": tables,
        "Base Grant Rules": grant_rules,
        "Base Grant Exceptions": exceptions or [],
        "Rbac Views": rbac_views,
        "Asset Classification": asset_classification or [],
        "Masking Rules": masking_rules or [],
        "Permission Bundles": [{"bundle": b} for b in bundles],
    }


# ---- labels (Base Tables) -----------------------------------------------

def test_labels_built_from_base_tables():
    """Each Base Tables row becomes a table label carrying its data_product.

    Expected: labels c.s.t1 to dp_a and c.s.t2 to dp_b, both of type table.
    """
    entries = generate_config(_wb())["labels"]["labels"]
    assert {(e["securable"]["name"], e["labels"]["data_product"]) for e in entries} == {
        ("c.s.t1", "dp_a"), ("c.s.t2", "dp_b")}
    assert all(e["securable"]["type"] == "table" for e in entries)


def test_target_schema_and_rls_column_not_yet_on_labels():
    """Labels carry only the data_product; target_schema and rls_column go to the view definitions,
    not onto labels.

    Expected: every label entry has only securable and labels, and every labels map has only
    data_product.
    """
    # gaps 2/3 deferred to view generation: labels carry only data_product, no routing fields.
    entries = generate_config(_wb())["labels"]["labels"]
    assert all(set(e) == {"securable", "labels"} for e in entries)
    assert all(set(e["labels"]) == {"data_product"} for e in entries)


# ---- Base Grant Rules ---------------------------------------------------

def test_base_grant_rule_shape():
    """A Base Grant Rules row becomes one base_grants rule with its id, group, bundle and label
    match.

    Expected: a single rule GR1 granting ADMIN to g_admin, matching object types [table, view] with
    data_product dp_a.
    """
    base = generate_config(_wb())["base_grants"]["grant_rules"]
    assert len(base) == 1
    r = base[0]
    assert r["rule_id"] == "GR1" and r["groups"] == ["g_admin"] and r["bundle"] == "ADMIN"
    assert r["match"]["object_types"] == ["table", "view"]
    assert r["match"]["labels"] == {"data_product": "dp_a"}


# ---- Rbac Views -> serving-schema grants --------------------------------

def test_rbac_views_emit_serving_schema_grants():
    """A Rbac Views row becomes a serving-schema grant rule.

    Expected: a single rule RBAC1 granting READ to g_read, including schema c.dm_x.
    """
    serving = generate_config(_wb())["rbac_view_grants"]["grant_rules"]
    assert len(serving) == 1
    r = serving[0]
    assert r["rule_id"] == "RBAC1" and r["groups"] == ["g_read"] and r["bundle"] == "READ"
    assert r["include"] == [{"type": "schema", "name": "c.dm_x"}]


def test_views_built_from_base_tables():
    """Each Base Tables row with a target_schema becomes a dynamic view in that schema, carrying
    rls_column only when one is set.

    Expected: views c.dm_x.t1 (source c.s.t1, rls_column portfolio_code) and c.dm_x.t2 (source
    c.s.t2, no rls_column, so no RLS).
    """
    views = generate_config(_wb())["rbac_views"]["views"]
    by_view = {v["view"]: v for v in views}
    assert set(by_view) == {"c.dm_x.t1", "c.dm_x.t2"}
    assert by_view["c.dm_x.t1"] == {"view": "c.dm_x.t1", "source": "c.s.t1", "rls_column": "portfolio_code"}
    assert by_view["c.dm_x.t2"] == {"view": "c.dm_x.t2", "source": "c.s.t2"}   # no rls_column -> no RLS


def test_tags_from_asset_classification():
    """Asset Classification rows become column tags, using the workbook's acm_-prefixed tag key as-
    is.

    Expected: column c.s.t1.name tagged acm_pii_type FULL_NAME, and c.s.t1.email tagged acm_pii_type
    EMAIL.
    """
    # tag_key is already acm_-namespaced in the workbook and used verbatim (no key translation).
    ac = [
        {"object": "c.s.t1.name", "tag_key": "acm_pii_type", "tag_value": "FULL_NAME"},
        {"object": "c.s.t1.email", "tag_key": "acm_pii_type", "tag_value": "EMAIL"},
    ]
    tags = generate_config(_wb(asset_classification=ac))["tags"]["tags"]
    by = {t["securable"]["name"]: t for t in tags}
    assert by["c.s.t1.name"] == {
        "securable": {"type": "column", "name": "c.s.t1.name"}, "tags": {"acm_pii_type": "FULL_NAME"}}
    assert by["c.s.t1.email"]["tags"] == {"acm_pii_type": "EMAIL"}


def test_column_masks_from_masking_rules():
    """A Masking Rules row becomes a column-mask policy over its whole attach scope.

    Expected: exactly one policy with scope c.gold, column_matching_tags {acm_pii_type: FULL_NAME}
    and function c.gov.acm_mask_full_name — and nothing else.
    """
    mr = [{"attach": "c.gold", "match_columns_key": "acm_pii_type", "match_columns_value": "FULL_NAME",
           "function": "c.gov.acm_mask_full_name"}]
    pols = generate_config(_wb(masking_rules=mr))["policies"]["column_mask_policies"]
    assert pols == [{"scope": "c.gold",
                     "column_matching_tags": {"acm_pii_type": "FULL_NAME"},
                     "function": "c.gov.acm_mask_full_name"}]


@pytest.mark.parametrize("col", ["when_key", "when_value"])
def test_masking_rule_when_columns_are_rejected(col):
    """A filled when_key / when_value fails generation instead of being silently ignored: table
    narrowing isn't supported, and a mask that seemed narrowed would really cover the whole scope.

    Expected: GeneratorError naming the row, the column and "not supported".
    """
    mr = [{"attach": "c.gold", col: "data_product", "match_columns_key": "acm_pii_type",
           "match_columns_value": "FULL_NAME", "function": "c.gov.acm_mask_full_name"}]
    with pytest.raises(GeneratorError, match=rf"Masking Rules row 0: {col} is not supported"):
        generate_config(_wb(masking_rules=mr))


def test_exceptions_carry_exp_id():
    """Base Grant Exceptions become include/exclude entries on their rule, each carrying its
    exception id.

    Expected: include [function c.s.f, exp_id EX1] and exclude [table c.s.t1, exp_id EX2] on rule
    GR1.
    """
    exceptions = [
        {"exception_id": "EX1", "rule_id": "GR1", "action": "include", "object_type": "function", "object": "c.s.f"},
        {"exception_id": "EX2", "rule_id": "GR1", "action": "exclude", "object_type": "table", "object": "c.s.t1"},
    ]
    base = generate_config(_wb(exceptions=exceptions))["base_grants"]["grant_rules"][0]
    assert base["include"] == [{"type": "function", "name": "c.s.f", "exp_id": "EX1"}]
    assert base["exclude"] == [{"type": "table", "name": "c.s.t1", "exp_id": "EX2"}]


def test_output_validates_and_compiles():
    """The generator's output for a valid workbook passes validation and compiles.

    Expected: validate_docs(...).ok and compile_config(...).ok are both True.
    """
    docs = generate_config(_wb())
    assert validate_docs(docs).ok
    assert compile_config(docs).ok


# ---- referential-integrity failures (all raise GeneratorError) ----------

@pytest.mark.parametrize("mutate", [
    pytest.param(lambda w: w["Base Grant Rules"][0].__setitem__("group_name", "ghost"), id="unknown_base_group"),
    pytest.param(lambda w: w["Base Grant Rules"][0].__setitem__("match_label_value", "dp_zzz"), id="unknown_data_product"),
    pytest.param(lambda w: w["Base Grant Rules"][0].__setitem__("bundle", "SUPERUSER"), id="unknown_bundle"),
    pytest.param(lambda w: w["Base Tables"][0].__setitem__("object_type", "widget"), id="bad_object_type"),
    pytest.param(lambda w: w["Base Grant Exceptions"].append(
        {"exception_id": "EX9", "rule_id": "GRX", "action": "include", "object_type": "table", "object": "c.s.z"}),
        id="orphan_exception"),
    pytest.param(lambda w: w["Rbac Views"][0].__setitem__("group_name", "ghost"), id="unknown_rbac_group"),
    pytest.param(lambda w: w["Rbac Views"][0].__setitem__("bundle", "SUPERUSER"), id="rbac_bad_bundle"),
    pytest.param(lambda w: w["Rbac Views"][0].pop("schema"), id="rbac_missing_schema"),
    pytest.param(lambda w: w["Base Tables"].append(
        {"catalog": "c", "schema": "s2", "object": "t1", "object_type": "table",
         "data_product": "dp_a", "target_schema": "c.dm_x"}), id="duplicate_view_target"),
    pytest.param(lambda w: w["Asset Classification"].append(
        {"object": "c.s.t1.x", "tag_key": "not_a_tag", "tag_value": "v"}), id="bad_tag_key"),
    pytest.param(lambda w: w["Asset Classification"].append(
        {"object": "c.s.t1.p", "tag_key": "pii_type", "tag_value": "FULL_NAME"}),
        id="short_tag_key_no_longer_mapped"),   # map ditched: bare 'pii_type' must be acm_-prefixed now
    pytest.param(lambda w: w["Masking Rules"].append(
        {"attach": "c.gold", "match_columns_key": "acm_bogus", "match_columns_value": "x",
         "function": "c.gov.acm_x"}), id="mask_bad_match_key"),
])
def test_referential_violations_raise(mutate):
    """For each broken workbook (unknown group, data product or bundle; bad object type; orphan
    exception; Rbac Views with an unknown group, bad bundle or missing schema; duplicate view
    target; bad or un-prefixed tag key; bad mask match key), generation refuses the workbook.

    Expected: GeneratorError is raised for every case.
    """
    wb = _wb()
    mutate(wb)
    with pytest.raises(GeneratorError):
        generate_config(wb)


# ---- integration against the signed-off v3 workbook ---------------------

def test_real_v3_workbook_generates_valid_config():
    """The signed-off v3 Access Matrix workbook (frozen copy in tests/fixtures) generates a valid,
    compilable config with the expected counts.

    Expected: valid and compilable; 10 labels, 7 base rules, 5 serving rules all on hera-dev.dm_esg,
    11 views (including dm_something.fact_portfolio_position, with RLS on
    dm_esg.fact_portfolio_position), 7 acm_pii_type column tags, and 4 mask policies using acm_
    functions.
    """
    wb = read_workbook(WORKBOOK, sheets=REQUIRED_SHEETS)
    # the v3 sign-off still has Masking Rules when_key/when_value (no longer supported): drop the two
    # columns as deleting them in Excel would (the frozen .xlsx itself is left untouched)
    wb["Masking Rules"] = [{k: v for k, v in r.items() if k not in ("when_key", "when_value")}
                           for r in wb["Masking Rules"]]
    docs = generate_config(wb)                       # raises if invalid
    assert validate_docs(docs).ok
    assert compile_config(docs).ok

    base = docs["base_grants"]["grant_rules"]
    serving = docs["rbac_view_grants"]["grant_rules"]
    assert len(docs["labels"]["labels"]) == 10       # 11 Base Tables rows, dup securable merged -> 10
    assert len(base) == 7                            # Base Grant Rules: admin x 7 data products
    assert len(serving) == 5                         # Rbac Views: 5 groups on the serving schema
    assert all(s["include"][0] == {"type": "schema", "name": "hera-dev.dm_esg"} for s in serving)

    views = docs["rbac_views"]["views"]                   # one per Base Tables row with a target_schema
    assert len(views) == 11                          # 10 -> hera-dev.dm_esg + 1 -> hera-dev.dm_something
    assert "hera-dev.dm_something.fact_portfolio_position" in {v["view"] for v in views}
    rls = {v["view"] for v in views if v.get("rls_column")}
    assert "hera-dev.dm_esg.fact_portfolio_position" in rls   # rls_column carried through

    tags = docs["tags"]["tags"]                      # from Asset Classification (7 pii_type rows)
    assert len(tags) == 7
    assert all(t["securable"]["type"] == "column" for t in tags)
    assert all(set(t["tags"]) <= {"acm_pii_type"} for t in tags)   # rls no longer materialized

    masks = docs["policies"]["column_mask_policies"]  # from Masking Rules (4 rows)
    assert len(masks) == 4
    assert all(m["function"].split(".")[-1].startswith("acm_") for m in masks)


def test_v3_workbook_as_signed_off_is_rejected_for_its_when_columns():
    """The v3 sign-off as-is fills Masking Rules when_key/when_value, which are no longer supported:
    generation fails and names every such cell, so the business deletes the two columns.

    Expected: GeneratorError with 8 "not supported" lines (4 rows x when_key + when_value).
    """
    wb = read_workbook(WORKBOOK, sheets=REQUIRED_SHEETS)
    with pytest.raises(GeneratorError) as exc:
        generate_config(wb)
    assert str(exc.value).count("is not supported") == 8


# ---- referential-integrity failures: each mistake -> its specific message ----

@pytest.mark.parametrize("mutate, message", [
    pytest.param(lambda w: w["Base Grant Rules"].append(dict(w["Base Grant Rules"][0])),
                 "'Base Grant Rules' tab: duplicate rule_id 'GR1'", id="duplicate_base_rule_id"),
    pytest.param(lambda w: w["Base Tables"][0].__setitem__("data_product", "dp_zzz"),
                 "Base Tables row 0: data_product 'dp_zzz' not in 'Data Products' tab",
                 id="base_table_unknown_data_product"),
    pytest.param(lambda w: w["Base Tables"][0].pop("catalog"),
                 "Base Tables row 0: missing catalog/schema/object", id="base_table_missing_catalog"),
    pytest.param(lambda w: w["Base Grant Exceptions"].append(
        {"exception_id": "EX9", "rule_id": "GR1", "action": "maybe", "object_type": "table",
         "object": "c.s.z"}),
        "Base Grant Exceptions EX9: action 'maybe' must be include/exclude", id="exception_bad_action"),
    pytest.param(lambda w: w["Permission Bundles"].__setitem__(slice(None), [{"bundle": "READ"}]),
                 "Base Grant Rules GR1: bundle 'ADMIN' not in 'Permission Bundles' tab",
                 id="bundle_not_in_bundles_tab"),
    pytest.param(lambda w: w["Rbac Views"].append(dict(w["Rbac Views"][0])),
                 "'Rbac Views' tab: duplicate rule_id 'RBAC1'", id="duplicate_rbac_rule_id"),
    pytest.param(lambda w: w["Asset Classification"].append({"tag_key": "acm_pii_type", "tag_value": "EMAIL"}),
                 "Asset Classification row 0: missing object", id="asset_missing_object"),
    pytest.param(lambda w: w["Masking Rules"].append(
        {"match_columns_key": "acm_pii_type", "match_columns_value": "EMAIL",
         "function": "c.gov.acm_mask_email"}),
        "Masking Rules row 0: missing attach (scope)", id="mask_missing_attach"),
])
def test_referential_violation_reports_specific_problem(mutate, message):
    """For each workbook mistake not covered above (duplicate rule ids, unknown data product, missing
    catalog, bad exception action, bundle missing from the Permission Bundles tab, asset row without
    an object, masking rule without attach), generation reports that exact problem.

    Expected: GeneratorError whose message contains the specific problem text for that mistake.
    """
    wb = _wb()
    mutate(wb)
    with pytest.raises(GeneratorError, match=re.escape(message)):
        generate_config(wb)


# ---- write_config: the generated docs land on disk, valid ----------------------

def test_write_config_writes_all_six_files(tmp_path):
    """write_config writes every generated document to its own YAML file, and the written directory
    is a valid config.

    Expected: exactly the 6 files (base_grants, rbac_view_grants, rbac_views, labels, tags, policies
    .yaml), each loading back to the generated document, and validate_dir on the folder is ok.
    """
    docs = generate_config(_wb())
    out = write_config(docs, tmp_path / "out")
    shorts = ["base_grants", "rbac_view_grants", "rbac_views", "labels", "tags", "policies"]
    assert sorted(f.name for f in out.iterdir()) == sorted(f"{s}.yaml" for s in shorts)
    for short in shorts:
        assert yaml.safe_load((out / f"{short}.yaml").read_text()) == docs[short], short
    assert validate_dir(out, reference={}).ok
