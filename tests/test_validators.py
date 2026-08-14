"""Tests for the JSON Schema config validator.

Each corruption is applied to the valid baseline (config/uat), written to a temp
dir, and run through the validator. We assert the valid baseline passes and every
corruption is caught (by the YAML schemas or the semantic layer).
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from framework.validation import validate_dir

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "config" / "uat"


def _load(name: str) -> dict:
    return yaml.safe_load((BASELINE / name).read_text())


def _write_config(tmp: Path, grants: dict, policies: dict, tags: dict) -> Path:
    (tmp / "grants.yaml").write_text(yaml.safe_dump(grants, sort_keys=False))
    (tmp / "policies.yaml").write_text(yaml.safe_dump(policies, sort_keys=False))
    (tmp / "tags.yaml").write_text(yaml.safe_dump(tags, sort_keys=False))
    return tmp


def _baseline() -> tuple[dict, dict, dict]:
    return _load("grants.yaml"), _load("policies.yaml"), _load("tags.yaml")


# ---- valid baseline ------------------------------------------------------

def test_baseline_valid():
    result = validate_dir(BASELINE)
    assert result.ok, "reported issues on the valid baseline:\n" + "\n".join(
        str(i) for i in result.sorted_issues()
    )


# ---- corruptions ---------------------------------------------------------

def _corrupt_bad_bundle(g, p, t):
    g["grant_rules"][0]["bundle"] = "SUPERUSER"          # not in enum

def _corrupt_extra_field(g, p, t):
    g["grant_rules"][0]["surprise"] = True               # closed record

def _corrupt_no_match_no_include(g, p, t):
    g["grant_rules"][0].pop("match", None)
    g["grant_rules"][0].pop("include", None)

def _corrupt_include_exclude_overlap(g, p, t):
    obj = {"type": "table", "name": "asset_prod.gold.investment_accounts"}
    g["grant_rules"][0]["include"] = [obj]
    g["grant_rules"][0]["exclude"] = [obj]

def _corrupt_bf_grain(g, p, t):
    g["grant_rules"][0]["match"]["object_types"] = ["function"]   # BF not allowed on function

def _corrupt_bad_fqn(g, p, t):
    g["grant_rules"][0]["include"] = [{"type": "table", "name": "only.two"}]

def _corrupt_mask_fn_prefix(g, p, t):
    p["column_mask_policies"][0]["function"] = "asset_prod.governance.mask_full_name"  # no acm_

def _corrupt_rls_too_many(g, p, t):
    p["row_filter_policies"][0]["column_matching_tags"] = [
        {"condition": {"acm_rls": "region"}, "as": "a"},
        {"condition": {"acm_rls": "legal_entity"}, "as": "b"},
        {"condition": {"acm_rls": "region"}, "as": "c"},
        {"condition": {"acm_rls": "legal_entity"}, "as": "d"},
    ]

def _corrupt_rls_dup_alias(g, p, t):
    p["row_filter_policies"][0]["column_matching_tags"] = [
        {"condition": {"acm_rls": "region"}, "as": "dup"},
        {"condition": {"acm_rls": "legal_entity"}, "as": "dup"},
    ]

def _corrupt_bf_on_column(g, p, t):
    t["tags"].append({
        "securable": {"type": "column", "name": "asset_prod.gold.investment_accounts.acct_no"},
        "tags": {"acm_business_function": "investments"},
    })

def _corrupt_dup_securable(g, p, t):
    t["tags"].append(copy.deepcopy(t["tags"][0]))        # repeat first securable

def _corrupt_bad_version(g, p, t):
    t["schema_version"] = "2.0"

def _corrupt_unknown_tag_key(g, p, t):
    t["tags"][0]["tags"] = {"acm_unknown": "x"}


CORRUPTIONS = {
    "bad_bundle": _corrupt_bad_bundle,
    "extra_field": _corrupt_extra_field,
    "no_match_no_include": _corrupt_no_match_no_include,
    "include_exclude_overlap": _corrupt_include_exclude_overlap,
    "bf_grain_illegal": _corrupt_bf_grain,
    "bad_fqn": _corrupt_bad_fqn,
    "mask_fn_prefix": _corrupt_mask_fn_prefix,
    "rls_too_many": _corrupt_rls_too_many,
    "rls_dup_alias": _corrupt_rls_dup_alias,
    "bf_on_column": _corrupt_bf_on_column,
    "dup_securable": _corrupt_dup_securable,
    "bad_version": _corrupt_bad_version,
    "unknown_tag_key": _corrupt_unknown_tag_key,
}


@pytest.mark.parametrize("name", CORRUPTIONS)
def test_corruption_caught(tmp_path, name):
    g, p, t = _baseline()
    CORRUPTIONS[name](g, p, t)
    cfg = _write_config(tmp_path, g, p, t)
    result = validate_dir(cfg)
    assert not result.ok, f"validator did NOT catch corruption {name!r}"


def test_missing_file(tmp_path):
    result = validate_dir(tmp_path)   # empty dir
    assert not result.ok
    assert any("file not found" in i.message for i in result.issues)
