"""Tests for the JSON Schema config validator.

Each corruption is applied to a minimal, self-contained valid baseline (built in-code,
not read from any shipped config dir), written to a temp dir, and run through the
validator. We assert the valid baseline passes and every corruption is caught (by the
YAML schemas or the semantic layer).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.validation import check_reference, validate_dir, validate_docs

REPO = Path(__file__).resolve().parents[1]


def _write_config(tmp: Path, grants: dict, policies: dict, tags: dict, labels: dict) -> Path:
    (tmp / "base_grants.yaml").write_text(yaml.safe_dump(grants, sort_keys=False))
    (tmp / "policies.yaml").write_text(yaml.safe_dump(policies, sort_keys=False))
    (tmp / "tags.yaml").write_text(yaml.safe_dump(tags, sort_keys=False))
    (tmp / "labels.yaml").write_text(yaml.safe_dump(labels, sort_keys=False))
    return tmp


def _baseline() -> tuple[dict, dict, dict, dict]:
    """A minimal, self-contained valid config the corruptions below mutate. Kept in-code so
    the tests don't depend on any shipped config dir. Exercises a business_function grant match,
    a column mask, an acm_pii_type column tag, and an asset label."""
    grants = {
        "schema_version": "1.0",
        "grant_rules": [
            {"groups": ["grp-read"], "bundle": "READ",
             "match": {"object_types": ["table"], "labels": {"business_function": "investments"}}},
        ],
    }
    policies = {
        "schema_version": "1.0",
        "column_mask_policies": [
            {"scope": "testcat.gold", "column_matching_tags": {"acm_pii_type": "FULL_NAME"},
             "function": "testcat.governance.acm_mask_full_name"},
        ],
    }
    tags = {
        "schema_version": "1.0",
        "tags": [
            {"securable": {"type": "column", "name": "testcat.gold.customers.full_name"},
             "tags": {"acm_pii_type": "FULL_NAME"}},
        ],
    }
    labels = {
        "schema_version": "1.0",
        "labels": [
            {"securable": {"type": "table", "name": "testcat.gold.orders"},
             "labels": {"business_function": "investments"}},
        ],
    }
    return grants, policies, tags, labels


# ---- valid baseline ------------------------------------------------------

def test_baseline_valid(tmp_path):
    cfg = _write_config(tmp_path, *_baseline())
    result = validate_dir(cfg)
    assert result.ok, "reported issues on the valid baseline:\n" + "\n".join(
        str(i) for i in result.sorted_issues()
    )


# ---- corruptions ---------------------------------------------------------

def _corrupt_bad_bundle(g, p, t, l):
    g["grant_rules"][0]["bundle"] = "SUPERUSER"          # not in enum

def _corrupt_extra_field(g, p, t, l):
    g["grant_rules"][0]["surprise"] = True               # closed record

def _corrupt_no_match_no_include(g, p, t, l):
    g["grant_rules"][0].pop("match", None)
    g["grant_rules"][0].pop("include", None)

def _corrupt_include_exclude_overlap(g, p, t, l):
    obj = {"type": "table", "name": "testcat.gold.orders"}
    g["grant_rules"][0]["include"] = [obj]
    g["grant_rules"][0]["exclude"] = [obj]

def _corrupt_bf_grain(g, p, t, l):
    g["grant_rules"][0]["match"]["object_types"] = ["function"]   # BF label not allowed on function

def _corrupt_bad_fqn(g, p, t, l):
    g["grant_rules"][0]["include"] = [{"type": "table", "name": "only.two"}]

def _corrupt_mask_fn_prefix(g, p, t, l):
    p["column_mask_policies"][0]["function"] = "testcat.governance.mask_full_name"  # no acm_

def _corrupt_bf_in_tags(g, p, t, l):
    # business_function is a label, not a tag: tags.yaml no longer accepts it anywhere.
    t["tags"].append({
        "securable": {"type": "column", "name": "testcat.gold.customers.acct_no"},
        "tags": {"acm_business_function": "investments"},
    })

def _corrupt_dup_securable(g, p, t, l):
    t["tags"].append(copy.deepcopy(t["tags"][0]))        # repeat first tag securable

def _corrupt_bad_version(g, p, t, l):
    t["schema_version"] = "2.0"

def _corrupt_unknown_tag_key(g, p, t, l):
    t["tags"][0]["tags"] = {"acm_unknown": "x"}

def _corrupt_label_on_column(g, p, t, l):
    # labels attach to asset securables only (ObjectRef excludes column).
    l["labels"].append({
        "securable": {"type": "column", "name": "testcat.gold.customers.acct_no"},
        "labels": {"business_function": "investments"},
    })

def _corrupt_dup_label_securable(g, p, t, l):
    l["labels"].append(copy.deepcopy(l["labels"][0]))    # repeat first label securable

def _corrupt_unknown_label_key(g, p, t, l):
    l["labels"][0]["labels"] = {"not_a_label_key": "x"}  # only business_function / data_product allowed


CORRUPTIONS = {
    "bad_bundle": _corrupt_bad_bundle,
    "extra_field": _corrupt_extra_field,
    "no_match_no_include": _corrupt_no_match_no_include,
    "include_exclude_overlap": _corrupt_include_exclude_overlap,
    "bf_grain_illegal": _corrupt_bf_grain,
    "bad_fqn": _corrupt_bad_fqn,
    "mask_fn_prefix": _corrupt_mask_fn_prefix,
    "bf_in_tags": _corrupt_bf_in_tags,
    "dup_securable": _corrupt_dup_securable,
    "bad_version": _corrupt_bad_version,
    "unknown_tag_key": _corrupt_unknown_tag_key,
    "label_on_column": _corrupt_label_on_column,
    "dup_label_securable": _corrupt_dup_label_securable,
    "unknown_label_key": _corrupt_unknown_label_key,
}


@pytest.mark.parametrize("name", CORRUPTIONS)
def test_corruption_caught(tmp_path, name):
    g, p, t, l = _baseline()
    CORRUPTIONS[name](g, p, t, l)
    cfg = _write_config(tmp_path, g, p, t, l)
    result = validate_dir(cfg)
    assert not result.ok, f"validator did NOT catch corruption {name!r}"


def test_missing_file(tmp_path):
    result = validate_dir(tmp_path)   # empty dir
    assert not result.ok
    assert any("file not found" in i.message for i in result.issues)


def test_data_product_label_validates(tmp_path):
    """business_function and data_product are both valid label keys; values are free strings."""
    g, p, t, l = _baseline()
    l["labels"].append({
        "securable": {"type": "table", "name": "testcat.gold.esg_waci"},
        "labels": {"data_product": "investment_esg"},
    })
    cfg = _write_config(tmp_path, g, p, t, l)
    assert validate_dir(cfg).ok


def test_labels_required(tmp_path):
    """labels.yaml is mandatory: an otherwise-valid config without it fails validation."""
    g, p, t, _l = _baseline()
    (tmp_path / "grants.yaml").write_text(yaml.safe_dump(g, sort_keys=False))
    (tmp_path / "policies.yaml").write_text(yaml.safe_dump(p, sort_keys=False))
    (tmp_path / "tags.yaml").write_text(yaml.safe_dump(t, sort_keys=False))
    # labels.yaml deliberately omitted
    result = validate_dir(tmp_path)
    assert not result.ok
    assert any(i.file == "labels.yaml" and "file not found" in i.message
               for i in result.issues)


# ---- reference allow-list (fail-open warning layer) ---------------------

_REFERENCE = {
    "groups": [{"name": "ok-group", "external_id": None}],   # external_id: Entra Object ID | null
    "source_schemas": ["testcat.approved"],    # base schemas
    "governance_schemas": ["testcat.gov"],
    "serving_schemas": ["testcat.serving"],     # dynamic-view destinations
    "data_products": ["approved_dp"],
    "governed_tags": {"acm_pii_type": ["FULL_NAME"]},   # keys = approved tag keys; lists = approved values
}


def test_check_reference_flags_offlist_vocabulary():
    docs = {
        "base_grants": {"grant_rules": [
            {"groups": ["ghost-group"], "bundle": "READ",
             "include": [{"type": "schema", "name": "testcat.rogue"}]}]},
        "tags": {"tags": [
            {"securable": {"type": "column", "name": "testcat.rogue.customers.national_id"},
             "tags": {"acm_pii_type": "SSN"}},
            {"securable": {"type": "column", "name": "testcat.approved.customers.notes"},
             "tags": {"made_up_key": "x"}}]},
    }
    issues = check_reference(docs, _REFERENCE)
    assert all(i.severity == "warning" and i.layer == "reference" for i in issues)
    msgs = " | ".join(i.message for i in issues)
    assert "'ghost-group'" in msgs        # off-list group
    assert "'testcat.rogue'" in msgs      # off-list schema
    assert "'SSN'" in msgs                # off-list value for a curated key
    assert "'made_up_key'" in msgs        # off-list tag key
    # a value under an uncurated key is not double-warned (only the key is flagged)
    assert not any("value 'x'" in i.message for i in issues)


def test_check_reference_flags_offlist_data_product():
    # data_product is read from both grant match.labels and labels.yaml; off-list -> warning.
    docs = {
        "base_grants": {"grant_rules": [
            {"groups": ["ok-group"], "bundle": "READ",
             "match": {"object_types": ["table"], "labels": {"data_product": "rogue_dp"}}}]},
        "labels": {"labels": [
            {"securable": {"type": "table", "name": "testcat.approved.orders"},
             "labels": {"data_product": "approved_dp"}}]},
    }
    issues = check_reference(docs, _REFERENCE)
    assert all(i.severity == "warning" for i in issues)
    msgs = " | ".join(i.message for i in issues)
    assert "data_product 'rogue_dp'" in msgs        # off-list -> warned
    assert "approved_dp" not in msgs                # listed -> clean


def test_check_reference_checks_mask_routing_value():
    # a column-mask policy's acm_pii_type routing value is governed vocabulary too.
    docs = {"policies": {"column_mask_policies": [
        {"scope": "testcat.approved", "column_matching_tags": {"acm_pii_type": "SSN"},
         "function": "testcat.approved.acm_mask_ssn"}]}}
    msgs = " | ".join(i.message for i in check_reference(docs, _REFERENCE))
    assert "tag value 'SSN' for key 'acm_pii_type'" in msgs        # off-list mask value warned

    docs["policies"]["column_mask_policies"][0]["column_matching_tags"] = {"acm_pii_type": "FULL_NAME"}
    assert check_reference(docs, _REFERENCE) == []                 # listed value -> clean


def test_check_reference_clean_when_all_listed():
    docs = {
        "base_grants": {"grant_rules": [
            {"groups": ["ok-group"], "bundle": "READ",
             "include": [{"type": "schema", "name": "testcat.approved"}]}]},
        "tags": {"tags": [
            {"securable": {"type": "column", "name": "testcat.approved.customers.full_name"},
             "tags": {"acm_pii_type": "FULL_NAME"}}]},
    }
    assert check_reference(docs, _REFERENCE) == []


def test_check_reference_flags_malformed_external_id():
    # a group's external_id must look like an Entra Object ID (GUID); null (local group) is fine.
    ref = {"groups": [
        {"name": "ok-group",   "external_id": "0f8fad5b-d9cb-469f-a165-70867728950e"},   # valid GUID
        {"name": "dev-group",  "external_id": None},                                     # local group
        {"name": "typo-group", "external_id": "hera-sg-eq-read-masked"}]}                # a name, not an id
    issues = check_reference({}, ref)
    assert [(i.file, i.path) for i in issues] == [("reference", "groups/2/external_id")]
    assert issues[0].severity == "warning" and "is not a GUID" in issues[0].message


def test_check_reference_flags_duplicate_or_unnamed_group():
    # a list can't enforce unique names the way a map would, so the reference layer does.
    ref = {"groups": [{"name": "g1", "external_id": None},
                      {"name": "g1", "external_id": None},        # duplicate
                      {"external_id": None}]}                     # no name
    msgs = {i.path: i.message for i in check_reference({}, ref)}
    assert msgs == {"groups/1/name": "group 'g1' is listed more than once",
                    "groups/2": "group entry has no name"}


def test_absent_reference_dimension_is_skipped():
    # 'groups' key absent => group vocabulary is "not curated" => no group warnings.
    ref = {"source_schemas": ["testcat.approved"]}
    docs = {"base_grants": {"grant_rules": [{"groups": ["ghost-group"], "bundle": "READ"}]}}
    assert check_reference(docs, ref) == []


def test_check_reference_view_target_must_be_serving():
    # a dynamic view's destination schema must be a declared serving_schema; the base source is a
    # normal approved-schema reference (union of source/serving/governance).
    docs = {"rbac_views": {"views": [
        {"view": "testcat.serving.v1", "source": "testcat.approved.t1"},    # ok: serving dest, source in union
        {"view": "testcat.approved.v2", "source": "testcat.approved.t2"}]}}  # bad: dest is a source schema
    msgs = " | ".join(i.message for i in check_reference(docs, _REFERENCE))
    assert "view target schema 'testcat.approved' is not a declared serving_schema" in msgs
    assert "'testcat.serving'" not in msgs   # a real serving destination is fine


def test_warnings_do_not_fail_validation():
    """A schema-valid config that references off-list vocabulary warns but stays ok."""
    g, p, t, l = _baseline()
    docs = {"base_grants.yaml": g, "policies.yaml": p, "tags.yaml": t, "labels.yaml": l}
    result = validate_docs(docs, reference={"groups": []})   # nothing approved -> warns on every group
    assert result.ok                       # warnings never flip ok
    assert result.warnings                 # ...but they are reported
    assert not result.errors


def test_validate_dir_autoloads_sibling_reference(tmp_path):
    cfg = tmp_path / "esg"
    cfg.mkdir()
    g, p, t, l = _baseline()
    g["grant_rules"][0]["groups"] = ["ghost-group"]
    _write_config(cfg, g, p, t, l)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "esg.yaml").write_text(yaml.safe_dump(
        {"schema_version": "1.0", "groups": [{"name": "ok-group", "external_id": None}]}, sort_keys=False))

    result = validate_dir(cfg)             # auto-discovers config/reference/esg.yaml
    assert result.ok                       # schema-valid; only a warning
    assert any("ghost-group" in w.message for w in result.warnings)

    # explicit reference={} disables the warning layer
    assert validate_dir(cfg, reference={}).warnings == []


def test_esg_config_has_no_reference_warnings():
    """The shipped config/esg must be clean against its own reference allow-list."""
    result = validate_dir(REPO / "config" / "esg")
    assert result.ok
    assert result.warnings == []


# ---- verbose 'what was checked' trace -----------------------------------

def test_checks_recorded_for_clean_config():
    by_target = {c.target: c for c in validate_dir(REPO / "config" / "esg").checks}
    assert by_target["base_grants.yaml"].status == "ok"
    assert "grant_rules=" in by_target["base_grants.yaml"].detail   # item counts surfaced
    assert by_target["rbac_views.yaml"].status == "ok"             # optional-but-present is checked
    assert by_target["schema_version"].status == "ok"
    assert by_target["reference"].status == "ok"                   # auto-loaded, no warnings


def test_checks_note_absent_optional(tmp_path):
    # an optional file that is absent is recorded as skipped (not an error)
    cfg = _write_config(tmp_path, *_baseline())   # base-only: no rbac_view_grants/rbac_views
    by_target = {c.target: c for c in validate_dir(cfg).checks}
    assert by_target["rbac_view_grants.yaml"].status == "skipped"
    assert by_target["rbac_views.yaml"].status == "skipped"


def test_checks_mark_issues_and_missing(tmp_path):
    g, p, t, l = _baseline()
    g["grant_rules"][0]["bundle"] = "SUPERUSER"                    # schema violation in base_grants
    cfg = _write_config(tmp_path, g, p, t, l)
    by_target = {c.target: c for c in validate_dir(cfg).checks}
    assert by_target["base_grants.yaml"].status == "issues"
    assert by_target["tags.yaml"].status == "ok"                  # unaffected file still ok

    empty = {c.target: c for c in validate_dir(tmp_path / "nope").checks}
    assert empty["base_grants.yaml"].status == "missing"


def test_reference_check_status_is_warnings_when_offlist(tmp_path):
    cfg = tmp_path / "esg"
    cfg.mkdir()
    g, p, t, l = _baseline()
    g["grant_rules"][0]["groups"] = ["ghost-group"]
    _write_config(cfg, g, p, t, l)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "esg.yaml").write_text(yaml.safe_dump(
        {"schema_version": "1.0", "groups": [{"name": "ok-group", "external_id": None}]}, sort_keys=False))
    by_target = {c.target: c for c in validate_dir(cfg).checks}
    assert by_target["reference"].status == "warnings"


def test_cli_verbose_lists_checks(capsys):
    from eastspring_uc_governance.validation.cli import main
    rc = main([str(REPO / "config" / "esg"), "--verbose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "checked" in out
    assert "vs grants.schema.yaml" in out           # per-file schema shown
    assert "reference:" in out                      # reference allow-list check shown
