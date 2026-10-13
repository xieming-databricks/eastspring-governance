"""Tests for rendering the packaged SQL objects (catalog / governance schema are placeholders)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from importlib import resources

from eastspring_uc_governance.sql_objects import render_sql_objects, sql_variables, unmasked_groups

REPO = Path(__file__).resolve().parents[1]
_REF = {"target_catalog": "testcat", "governance_schemas": ["testcat.gov"]}


def test_sql_variables_from_reference():
    assert sql_variables(_REF) == {"catalog": "testcat", "governance_schema": "gov",
                                   "unmasked_condition": "FALSE"}   # no groups -> nobody unmasked


@pytest.mark.parametrize("ref, msg", [
    ({"governance_schemas": ["testcat.gov"]}, "no target_catalog"),
    ({"target_catalog": "testcat", "governance_schemas": []}, "exactly one"),
    ({"target_catalog": "testcat", "governance_schemas": ["testcat.a", "testcat.b"]}, "exactly one"),
    ({"target_catalog": "testcat", "governance_schemas": ["other.gov"]}, "exactly one"),  # wrong catalog
])
def test_sql_variables_fail_loudly(ref, msg):
    with pytest.raises(ValueError, match=msg):
        sql_variables(ref)


def test_render_fills_every_placeholder_in_every_packaged_file():
    objects = render_sql_objects(_REF)
    names = [n for n, _ in objects]
    assert names == sorted(names) and "group_portfolio_access.sql" in names
    assert "acm_is_unmasked.sql" not in names                          # inlined, no shared function
    assert len([n for n in names if n.startswith("acm_mask_")]) == 4
    for name, sql in objects:
        assert "${" not in sql and "hera-dev" not in sql, name          # no placeholder / env leftovers
        assert "`testcat`.`gov`." in sql, name
        assert not sql.rstrip().endswith(";"), name                      # spark.sql: single statement


def test_render_for_the_shipped_esg_reference():
    ref = yaml.safe_load((REPO / "config" / "reference" / "esg.yaml").read_text())
    sql = dict(render_sql_objects(ref))["group_portfolio_access.sql"]
    assert "CREATE OR REPLACE MATERIALIZED VIEW `hera-dev`.`governance`.group_portfolio_access" in sql
    assert "FROM `hera-dev`.`governance`.entitlements" in sql


def test_backtick_in_a_name_stays_quoted():
    assert sql_variables({"target_catalog": "we`ird", "governance_schemas": ["we`ird.g"]})["catalog"] == "we``ird"


def test_every_mask_inlines_the_same_generated_condition():
    # "who reads unmasked" comes from the reference; every mask UDF carries the identical condition,
    # so they always agree, and no mask depends on another object existing first.
    objects = dict(render_sql_objects({**_REF, "groups": _GROUPS}))
    masks = {n: sql for n, sql in objects.items() if n.startswith("acm_mask_")}
    assert len(masks) == 4
    cond = sql_variables({**_REF, "groups": _GROUPS})["unmasked_condition"]
    for name, sql in masks.items():
        assert f"WHEN {cond}\n         THEN v" in sql, name
        assert "acm_is_unmasked" not in sql, name


# ---- unmasked groups: generated from the reference's `groups` ---------------

_GROUPS = [{"name": "hera-all-esg-read-unmasked", "external_id": None},
           {"name": "hera-all-esg-read-masked", "external_id": None},       # "masked", not "unmasked"
           {"name": "hera-all-admin-admin-unmasked", "external_id": None},
           {"name": "hera-all-esg-read-unmasked", "external_id": None}]     # duplicate


def test_unmasked_groups_are_the_unmasked_suffix_ones_in_order():
    assert unmasked_groups({"groups": _GROUPS}) == ["hera-all-esg-read-unmasked",
                                                     "hera-all-admin-admin-unmasked"]
    assert unmasked_groups({"groups": []}) == [] and unmasked_groups({}) == []


def test_mask_when_clause_is_generated():
    sql = dict(render_sql_objects({**_REF, "groups": _GROUPS}))["acm_mask_passport.sql"]
    assert ("RETURN CASE\n"
            "         WHEN is_account_group_member('hera-all-esg-read-unmasked')\n"
            "           OR is_account_group_member('hera-all-admin-admin-unmasked')\n"
            "         THEN v\n") in sql
    assert "hera-all-esg-read-masked" not in sql


def test_masks_fail_closed_without_unmasked_groups():
    sql = dict(render_sql_objects({**_REF, "groups": [{"name": "only-masked", "external_id": None}]}))
    for name in ("acm_mask_email.sql", "acm_mask_full_name.sql", "acm_mask_identifier.sql", "acm_mask_passport.sql"):
        assert "WHEN FALSE\n         THEN v" in sql[name], name       # nobody unmasked


def test_group_name_quote_is_escaped():
    cond = sql_variables({**_REF, "groups": [{"name": "o'brien-unmasked"}]})["unmasked_condition"]
    assert cond == "is_account_group_member('o''brien-unmasked')"


def test_multiline_placeholder_never_in_a_comment():
    # unmasked_condition spans several lines; substituted inside a `--` comment, its 2nd+ lines would
    # escape the comment and break the SQL. It may only appear on code lines.
    for f in resources.files("eastspring_uc_governance").joinpath("sql").iterdir():
        if f.name.endswith(".sql"):
            for line in f.read_text().splitlines():
                if "${unmasked_condition}" in line:
                    assert not line.lstrip().startswith("--") and "--" not in line.split("${unmasked_condition}")[0], (f.name, line)


def test_shipped_esg_reference_unmasked_groups():
    ref = yaml.safe_load((REPO / "config" / "reference" / "esg.yaml").read_text())
    assert unmasked_groups(ref) == ["hera-all-esg-read-unmasked", "hera-all-admin-admin-unmasked"]
