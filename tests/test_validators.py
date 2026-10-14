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
    """The minimal in-code baseline config (a grant rule, a column mask, a column tag and a label)
    is valid.

    Expected: validate_dir reports no issues (result.ok is True).
    """
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
    """For each corruption in CORRUPTIONS (bad bundle, unknown field, rule with no match or include,
    include/exclude overlap, illegal label grain, bad FQN, mask function without acm_,
    business_function used as a tag, duplicate securables, wrong schema_version, unknown tag or
    label key, label on a column), validation catches the broken config.

    Expected: result.ok is False for every corruption.
    """
    g, p, t, l = _baseline()
    CORRUPTIONS[name](g, p, t, l)
    cfg = _write_config(tmp_path, g, p, t, l)
    result = validate_dir(cfg)
    assert not result.ok, f"validator did NOT catch corruption {name!r}"


def test_missing_file(tmp_path):
    """Validating an empty directory reports the required files as missing.

    Expected: result.ok is False with a "file not found" issue.
    """
    result = validate_dir(tmp_path)   # empty dir
    assert not result.ok
    assert any("file not found" in i.message for i in result.issues)


def test_data_product_label_validates(tmp_path):
    """data_product is an accepted label key alongside business_function, with a free-string value.

    Expected: the baseline plus a data_product label still validates (result.ok is True).
    """
    g, p, t, l = _baseline()
    l["labels"].append({
        "securable": {"type": "table", "name": "testcat.gold.esg_waci"},
        "labels": {"data_product": "investment_esg"},
    })
    cfg = _write_config(tmp_path, g, p, t, l)
    assert validate_dir(cfg).ok


def test_labels_required(tmp_path):
    """labels.yaml is a required file: an otherwise-valid config without it fails.

    Expected: result.ok is False with a "file not found" issue for labels.yaml.
    """
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
    """The reference check warns about vocabulary that isn't on the allow-list: an unknown group,
    schema, tag value and tag key.

    Expected: only reference-layer warnings, naming ghost-group, testcat.rogue, SSN and made_up_key;
    the value under the unknown key is not warned about separately.
    """
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
    """A data_product not on the allow-list is warned about, whether used in a grant match or a
    label.

    Expected: a warning for data_product rogue_dp and none for the listed approved_dp.
    """
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
    """The acm_pii_type value a mask policy routes on is checked against the allow-list too.

    Expected: a warning for tag value SSN for key acm_pii_type; no issues once the value is changed
    to the listed FULL_NAME.
    """
    # a column-mask policy's acm_pii_type routing value is governed vocabulary too.
    docs = {"policies": {"column_mask_policies": [
        {"scope": "testcat.approved", "column_matching_tags": {"acm_pii_type": "SSN"},
         "function": "testcat.approved.acm_mask_ssn"}]}}
    msgs = " | ".join(i.message for i in check_reference(docs, _REFERENCE))
    assert "tag value 'SSN' for key 'acm_pii_type'" in msgs        # off-list mask value warned

    docs["policies"]["column_mask_policies"][0]["column_matching_tags"] = {"acm_pii_type": "FULL_NAME"}
    assert check_reference(docs, _REFERENCE) == []                 # listed value -> clean


def test_check_reference_clean_when_all_listed():
    """A config that uses only allow-listed groups, schemas and tags passes the reference check.

    Expected: no issues.
    """
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
    """A group's external_id must look like an Entra Object ID (GUID); a valid GUID and null both
    pass.

    Expected: exactly one warning, at groups/2/external_id, saying the value "is not a GUID".
    """
    # a group's external_id must look like an Entra Object ID (GUID); null (local group) is fine.
    ref = {"groups": [
        {"name": "ok-group",   "external_id": "0f8fad5b-d9cb-469f-a165-70867728950e"},   # valid GUID
        {"name": "dev-group",  "external_id": None},                                     # local group
        {"name": "typo-group", "external_id": "hera-sg-eq-read-masked"}]}                # a name, not an id
    issues = check_reference({}, ref)
    assert [(i.file, i.path) for i in issues] == [("reference", "groups/2/external_id")]
    assert issues[0].severity == "warning" and "is not a GUID" in issues[0].message


def test_check_reference_flags_duplicate_or_unnamed_group():
    """The reference check catches a group listed twice and a group entry without a name.

    Expected: exactly two messages: groups/1/name "group 'g1' is listed more than once" and groups/2
    "group entry has no name".
    """
    # a list can't enforce unique names the way a map would, so the reference layer does.
    ref = {"groups": [{"name": "g1", "external_id": None},
                      {"name": "g1", "external_id": None},        # duplicate
                      {"external_id": None}]}                     # no name
    msgs = {i.path: i.message for i in check_reference({}, ref)}
    assert msgs == {"groups/1/name": "group 'g1' is listed more than once",
                    "groups/2": "group entry has no name"}


def test_absent_reference_dimension_is_skipped():
    """A dimension missing from the reference (here groups) counts as not curated, so its use isn't
    checked.

    Expected: no issues, even though ghost-group is used.
    """
    # 'groups' key absent => group vocabulary is "not curated" => no group warnings.
    ref = {"source_schemas": ["testcat.approved"]}
    docs = {"base_grants": {"grant_rules": [{"groups": ["ghost-group"], "bundle": "READ"}]}}
    assert check_reference(docs, ref) == []


def test_check_reference_view_target_must_be_serving():
    """A dynamic view's destination schema must be a declared serving schema, while its source only
    needs to be an approved schema.

    Expected: a warning that view target schema testcat.approved is not a declared serving_schema,
    and nothing about the real serving schema testcat.serving.
    """
    # a dynamic view's destination schema must be a declared serving_schema; the base source is a
    # normal approved-schema reference (union of source/serving/governance).
    docs = {"rbac_views": {"views": [
        {"view": "testcat.serving.v1", "source": "testcat.approved.t1"},    # ok: serving dest, source in union
        {"view": "testcat.approved.v2", "source": "testcat.approved.t2"}]}}  # bad: dest is a source schema
    msgs = " | ".join(i.message for i in check_reference(docs, _REFERENCE))
    assert "view target schema 'testcat.approved' is not a declared serving_schema" in msgs
    assert "'testcat.serving'" not in msgs   # a real serving destination is fine


def test_warnings_do_not_fail_validation():
    """Reference warnings never make an otherwise schema-valid config fail.

    Expected: result.ok is True and there are no errors, but warnings are reported.
    """
    g, p, t, l = _baseline()
    docs = {"base_grants.yaml": g, "policies.yaml": p, "tags.yaml": t, "labels.yaml": l}
    result = validate_docs(docs, reference={"groups": []})   # nothing approved -> warns on every group
    assert result.ok                       # warnings never flip ok
    assert result.warnings                 # ...but they are reported
    assert not result.errors


def test_validate_dir_autoloads_sibling_reference(tmp_path):
    """validate_dir finds config/reference/<dir>.yaml next to the config dir automatically; passing
    reference={} turns the check off.

    Expected: the config stays valid with a warning about ghost-group, and no warnings at all with
    reference={}.
    """
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
    """The shipped config/esg is clean against its own reference allow-list.

    Expected: result.ok is True and there are no warnings.
    """
    result = validate_dir(REPO / "config" / "esg")
    assert result.ok
    assert result.warnings == []


# ---- verbose 'what was checked' trace -----------------------------------

def test_checks_recorded_for_clean_config():
    """Verbose mode records each check performed on config/esg with its status.

    Expected: base_grants.yaml ok with a grant_rules count, rbac_views.yaml ok, schema_version ok,
    and reference ok.
    """
    by_target = {c.target: c for c in validate_dir(REPO / "config" / "esg").checks}
    assert by_target["base_grants.yaml"].status == "ok"
    assert "grant_rules=" in by_target["base_grants.yaml"].detail   # item counts surfaced
    assert by_target["rbac_views.yaml"].status == "ok"             # optional-but-present is checked
    assert by_target["schema_version"].status == "ok"
    assert by_target["reference"].status == "ok"                   # auto-loaded, no warnings


def test_checks_note_absent_optional(tmp_path):
    """An optional file that is absent is recorded as skipped, not as an error.

    Expected: rbac_view_grants.yaml and rbac_views.yaml both have status skipped.
    """
    # an optional file that is absent is recorded as skipped (not an error)
    cfg = _write_config(tmp_path, *_baseline())   # base-only: no rbac_view_grants/rbac_views
    by_target = {c.target: c for c in validate_dir(cfg).checks}
    assert by_target["rbac_view_grants.yaml"].status == "skipped"
    assert by_target["rbac_views.yaml"].status == "skipped"


def test_checks_mark_issues_and_missing(tmp_path):
    """The check list marks a file with schema problems as issues and a missing required file as
    missing, while other files stay ok.

    Expected: base_grants.yaml issues and tags.yaml ok in the broken config; base_grants.yaml
    missing for a non-existent directory.
    """
    g, p, t, l = _baseline()
    g["grant_rules"][0]["bundle"] = "SUPERUSER"                    # schema violation in base_grants
    cfg = _write_config(tmp_path, g, p, t, l)
    by_target = {c.target: c for c in validate_dir(cfg).checks}
    assert by_target["base_grants.yaml"].status == "issues"
    assert by_target["tags.yaml"].status == "ok"                  # unaffected file still ok

    empty = {c.target: c for c in validate_dir(tmp_path / "nope").checks}
    assert empty["base_grants.yaml"].status == "missing"


def test_reference_check_status_is_warnings_when_offlist(tmp_path):
    """The reference entry in the check list shows warnings when the config uses an off-list group.

    Expected: the reference check's status is warnings.
    """
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
    """The validation CLI's --verbose flag lists every check it performed.

    Expected: exit code 0, and the output contains "checked", "vs grants.schema.yaml" and
    "reference:".
    """
    from eastspring_uc_governance.validation.cli import main
    rc = main([str(REPO / "config" / "esg"), "--verbose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "checked" in out
    assert "vs grants.schema.yaml" in out           # per-file schema shown
    assert "reference:" in out                      # reference allow-list check shown


def test_skip_base_grants_must_be_boolean():
    """skip_base_grants must be a real boolean, since a quoted "true" would silently not skip.

    Expected: no issues for True or False; for "true", exactly the warning "skip_base_grants must be
    true or false, got 'true' (treated as false)".
    """
    for ok in (True, False):
        assert check_reference({}, {"skip_base_grants": ok}) == []
    msgs = [i.message for i in check_reference({}, {"skip_base_grants": "true"})]
    assert msgs == ["skip_base_grants must be true or false, got 'true' (treated as false)"]


# ---- summary line, unreadable files, missing / malformed documents ------

def _docs():
    g, p, t, l = _baseline()
    return {"base_grants.yaml": g, "policies.yaml": p, "tags.yaml": t, "labels.yaml": l}


def test_summary_text_for_clean_warning_and_error_results():
    """The one-line summary distinguishes a clean result, warnings only, and errors.

    Expected: "OK <docs> — no issues" when clean; "OK <docs> — 0 error(s), 1 warning(s)" when the
    only problem is an off-list group; a line starting "FAIL <docs> —" when there is an error.
    """
    assert validate_docs(_docs()).summary() == "OK <docs> — no issues"
    assert validate_docs(_docs(), reference={"groups": []}).summary() == \
        "OK <docs> — 0 error(s), 1 warning(s)"
    bad = _docs()
    bad["base_grants.yaml"]["grant_rules"][0]["bundle"] = "SUPERUSER"
    assert validate_docs(bad).summary().startswith("FAIL <docs> — ")


@pytest.mark.parametrize("filename", ["tags.yaml", "rbac_views.yaml"])   # required and optional
def test_yaml_syntax_error_is_reported(tmp_path, filename):
    """For a required file (tags.yaml) and an optional one (rbac_views.yaml), a file that isn't valid
    YAML is reported instead of crashing the validator.

    Expected: result.ok is False, an issue for that file starting "YAML parse error", and that
    file's check status is issues.
    """
    cfg = _write_config(tmp_path, *_baseline())
    (cfg / filename).write_text("tags: [unclosed\n")
    result = validate_dir(cfg, reference={})
    assert not result.ok
    assert any(i.file == filename and i.message.startswith("YAML parse error") for i in result.issues)
    assert {c.target: c for c in result.checks}[filename].status == "issues"


def test_unparseable_reference_is_skipped(tmp_path):
    """An auto-discovered reference file that isn't valid YAML is skipped rather than failing.

    Expected: the config still validates (ok) and the reference check status is skipped.
    """
    cfg = tmp_path / "esg"
    cfg.mkdir()
    _write_config(cfg, *_baseline())
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference" / "esg.yaml").write_text("groups: [unclosed\n")
    result = validate_dir(cfg)
    assert result.ok
    assert {c.target: c for c in result.checks}["reference"].status == "skipped"


def test_validate_docs_reports_missing_document():
    """validate_docs, given in-memory documents, reports a required document that wasn't supplied.

    Expected: result.ok is False, a "document not provided" issue for tags.yaml, and its check
    status is missing.
    """
    docs = _docs()
    del docs["tags.yaml"]
    result = validate_docs(docs)
    assert not result.ok
    assert any(i.file == "tags.yaml" and i.message == "document not provided" for i in result.issues)
    assert {c.target: c for c in result.checks}["tags.yaml"].status == "missing"


def test_non_mapping_documents_do_not_crash():
    """Documents, entries and references of the wrong shape (lists or strings where mappings are
    expected) are reported as schema problems, and the semantic and reference layers skip them
    instead of crashing.

    Expected: validate_docs on list-valued documents returns ok False without raising; check_reference
    with non-mapping entries, or with a non-mapping reference, returns no issues.
    """
    result = validate_docs({name: ["not", "a", "mapping"] for name in _docs()})
    assert not result.ok
    shapeless = {"base_grants": {"grant_rules": ["x"]}, "labels": {"labels": ["x"]},
                 "tags": {"tags": ["x"]}, "policies": {"column_mask_policies": ["x"]},
                 "rbac_views": {"views": ["x"]}}
    assert check_reference(shapeless, {"groups": []}) == []
    assert check_reference(_docs(), "not-a-reference") == []
