"""Tests for the Access-Matrix generator (pure transform + one real-workbook integration)."""
from __future__ import annotations

from pathlib import Path

import pytest

from framework.compile import compile_config
from framework.generate import generate_config, read_workbook
from framework.generate.generator import GeneratorError
from framework.validation import validate_docs

REPO = Path(__file__).resolve().parents[1]
WORKBOOK = REPO / "aux_files" / "signed_off" / "access-matrix-esg_workshop-v2-signoff.xlsx"


def _wb(*, groups=("g_read",), data_products=("dp_a", "dp_b"), tables=None,
        grant_rules=None, exceptions=None, bundles=("READ", "WRITE", "OWNER", "ADMIN")):
    """Build a minimal, otherwise-valid workbook (tab -> row-dicts)."""
    if tables is None:
        tables = [
            {"catalog": "c", "schema": "s", "object": "t1", "object_type": "table", "data_product": "dp_a"},
            {"catalog": "c", "schema": "s", "object": "t2", "object_type": "table", "data_product": "dp_b"},
        ]
    if grant_rules is None:
        grant_rules = [{"rule_id": "GR1", "group_name": "g_read", "match_label_key": "data_product",
                        "match_label_value": "dp_a", "bundle": "READ", "object_types": "table; view"}]
    return {
        "Groups": [{"group_name": g} for g in groups],
        "Data Products": [{"data_product": d} for d in data_products],
        "Tables": tables,
        "Grant Rules": grant_rules,
        "Grant Exceptions": exceptions or [],
        "Permission Bundles": [{"bundle": b} for b in bundles],
    }


# ---- happy-path transform -----------------------------------------------

def test_labels_built_from_tables():
    docs = generate_config(_wb())
    entries = docs["labels"]["labels"]
    assert {(e["securable"]["name"], e["labels"]["data_product"]) for e in entries} == {
        ("c.s.t1", "dp_a"), ("c.s.t2", "dp_b")}
    assert all(e["securable"]["type"] == "table" for e in entries)


def test_grant_rule_shape_and_object_types_parsing():
    rule = generate_config(_wb())["grants"]["grant_rules"][0]
    assert rule["rule_id"] == "GR1"                                # workbook rule_id preserved
    assert rule["groups"] == ["g_read"]
    assert rule["bundle"] == "READ"
    assert rule["match"]["object_types"] == ["table", "view"]      # "table; view" parsed
    assert rule["match"]["labels"] == {"data_product": "dp_a"}


def test_exceptions_carry_exp_id_and_attach_to_matching_rule_only():
    exceptions = [
        {"exception_id": "EX1", "rule_id": "GR1", "action": "include", "object_type": "function", "object": "c.s.f"},
        {"exception_id": "EX2", "rule_id": "GR1", "action": "exclude", "object_type": "table", "object": "c.s.t1"},
    ]
    rule = generate_config(_wb(exceptions=exceptions))["grants"]["grant_rules"][0]
    assert rule["include"] == [{"type": "function", "name": "c.s.f", "exp_id": "EX1"}]
    assert rule["exclude"] == [{"type": "table", "name": "c.s.t1", "exp_id": "EX2"}]


def test_tags_and_policies_are_empty_stubs():
    docs = generate_config(_wb())
    assert docs["tags"] == {"schema_version": "1.0", "tags": []}
    assert docs["policies"] == {"schema_version": "1.0"}


def test_output_validates_and_compiles():
    docs = generate_config(_wb())
    assert validate_docs(docs).ok
    st = compile_config(docs)
    assert st.ok
    # GR1 (READ on dp_a-labeled tables) grants SELECT on c.s.t1 only
    assert {g.securable_fqn for g in st.grants if g.privilege_type == "SELECT"} == {"c.s.t1"}


# ---- referential-integrity failures (all raise GeneratorError) ----------

@pytest.mark.parametrize("mutate", [
    pytest.param(lambda w: w["Grant Rules"][0].__setitem__("group_name", "ghost"), id="unknown_group"),
    pytest.param(lambda w: w["Grant Rules"][0].__setitem__("match_label_value", "dp_zzz"), id="unknown_data_product"),
    pytest.param(lambda w: w["Grant Rules"][0].__setitem__("bundle", "SUPERUSER"), id="unknown_bundle"),
    pytest.param(lambda w: w["Grant Rules"][0].__setitem__("match_label_key", "team"), id="unknown_label_key"),
    pytest.param(lambda w: w["Grant Rules"][0].__setitem__("object_types", ""), id="empty_object_types"),
    pytest.param(lambda w: w["Tables"][0].__setitem__("object_type", "widget"), id="bad_table_object_type"),
    pytest.param(lambda w: w["Tables"][0].__setitem__("data_product", "dp_zzz"), id="table_unknown_data_product"),
    pytest.param(lambda w: w["Grant Exceptions"].append(
        {"exception_id": "EX9", "rule_id": "GRX", "action": "include", "object_type": "table", "object": "c.s.z"}),
        id="orphan_exception"),
    pytest.param(lambda w: w["Grant Rules"].append(
        {"rule_id": "GR1", "group_name": "g_read", "match_label_key": "data_product",
         "match_label_value": "dp_a", "bundle": "READ", "object_types": "table"}),
        id="duplicate_rule_id"),
])
def test_referential_violations_raise(mutate):
    wb = _wb()
    mutate(wb)
    with pytest.raises(GeneratorError):
        generate_config(wb)


# ---- integration against the signed-off ESG workbook --------------------

@pytest.mark.skipif(not WORKBOOK.exists(), reason="signed-off workbook not present")
def test_real_workbook_generates_valid_config():
    from framework.generate.generator import REQUIRED_SHEETS
    wb = read_workbook(WORKBOOK, sheets=REQUIRED_SHEETS)
    docs = generate_config(wb)                       # raises if invalid
    assert validate_docs(docs).ok
    assert compile_config(docs).ok

    assert len(docs["labels"]["labels"]) == 10       # 10 tables in the Tables tab
    assert len(docs["grants"]["grant_rules"]) == 35  # GR001..GR035
    # EX001 (include) + EX002 (exclude) both land on GR001 (the first rule), with exp_ids
    gr001 = docs["grants"]["grant_rules"][0]
    assert gr001["rule_id"] == "GR001"
    assert gr001["include"] == [
        {"type": "table", "name": "hera-dev.governance.example_esg_helper", "exp_id": "EX001"}]
    assert gr001["exclude"] == [
        {"type": "table", "name": "hera-dev.gold.example_restricted_esg", "exp_id": "EX002"}]
