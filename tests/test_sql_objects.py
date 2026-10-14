"""Tests for rendering the packaged SQL objects (catalog / governance schema are placeholders)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from importlib import resources

from eastspring_uc_governance.sql_objects import render_sql_objects, sql_variables, unmasked_groups

FIXTURE_REFERENCE = Path(__file__).resolve().parent / "fixtures" / "config" / "reference" / "esg.yaml"
_REF = {"target_catalog": "testcat", "governance_schemas": ["testcat.gov"]}


def test_sql_variables_from_reference():
    """The placeholder values come from the reference: catalog from target_catalog,
    governance_schema from the single governance schema, and the unmasked condition from groups.

    Expected: {catalog: testcat, governance_schema: gov, unmasked_condition: FALSE}; FALSE because
    the reference declares no groups.
    """
    assert sql_variables(_REF) == {"catalog": "testcat", "governance_schema": "gov",
                                   "unmasked_condition": "FALSE"}   # no groups -> nobody unmasked


@pytest.mark.parametrize("ref, msg", [
    ({"governance_schemas": ["testcat.gov"]}, "no target_catalog"),
    ({"target_catalog": "testcat", "governance_schemas": []}, "exactly one"),
    ({"target_catalog": "testcat", "governance_schemas": ["testcat.a", "testcat.b"]}, "exactly one"),
    ({"target_catalog": "testcat", "governance_schemas": ["other.gov"]}, "exactly one"),  # wrong catalog
])
def test_sql_variables_fail_loudly(ref, msg):
    """For each invalid reference (no target_catalog; zero, two, or wrong-catalog governance
    schemas), deriving the placeholder values refuses to guess.

    Expected: a ValueError whose message mentions the specific problem ("no target_catalog" or
    "exactly one").
    """
    with pytest.raises(ValueError, match=msg):
        sql_variables(ref)


def test_render_fills_every_placeholder_in_every_packaged_file():
    """Rendering fills every placeholder in every packaged SQL file and produces one statement per
    file, in name order.

    Expected: the 4 acm_mask_* UDFs plus group_portfolio_access, sorted, with no acm_is_unmasked; no
    leftover ${ or hera-dev, every object qualified as `testcat`.`gov`., and no trailing semicolon.
    """
    objects = render_sql_objects(_REF)
    names = [n for n, _ in objects]
    assert names == sorted(names) and "group_portfolio_access.sql" in names
    assert "acm_is_unmasked.sql" not in names                          # inlined, no shared function
    assert len([n for n in names if n.startswith("acm_mask_")]) == 4
    for name, sql in objects:
        assert "${" not in sql and "hera-dev" not in sql, name          # no placeholder / env leftovers
        assert "`testcat`.`gov`." in sql, name
        assert not sql.rstrip().endswith(";"), name                      # spark.sql: single statement


def test_render_for_the_esg_fixture_reference():
    """The group_portfolio_access MV renders correctly against the ESG fixture reference.

    Expected: the SQL creates `hera-dev`.`governance`.group_portfolio_access and reads from `hera-
    dev`.`governance`.entitlements.
    """
    ref = yaml.safe_load(FIXTURE_REFERENCE.read_text())
    sql = dict(render_sql_objects(ref))["group_portfolio_access.sql"]
    assert "CREATE OR REPLACE MATERIALIZED VIEW `hera-dev`.`governance`.group_portfolio_access" in sql
    assert "FROM `hera-dev`.`governance`.entitlements" in sql


def test_backtick_in_a_name_stays_quoted():
    """A backtick inside a catalog name is escaped so the name stays inside its backtick-quoted
    identifier.

    Expected: the catalog value we`ird becomes we``ird.
    """
    assert sql_variables({"target_catalog": "we`ird", "governance_schemas": ["we`ird.g"]})["catalog"] == "we``ird"


def test_every_mask_inlines_the_same_generated_condition():
    """Every mask UDF inlines the same generated unmasked condition, so the masks always agree and
    none depends on another object.

    Expected: all 4 masks contain WHEN <condition> THEN v, and none references acm_is_unmasked.
    """
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
    """Only groups whose name ends in "unmasked" are selected, in reference order and without
    duplicates.

    Expected: [hera-all-esg-read-unmasked, hera-all-admin-admin-unmasked]; hera-all-esg-read-masked
    and the duplicate are excluded; [] for no or empty groups.
    """
    assert unmasked_groups({"groups": _GROUPS}) == ["hera-all-esg-read-unmasked",
                                                     "hera-all-admin-admin-unmasked"]
    assert unmasked_groups({"groups": []}) == [] and unmasked_groups({}) == []


def test_mask_when_clause_is_generated():
    """A mask's WHEN clause is generated from the unmasked groups, OR-ed and indented under WHEN.

    Expected: acm_mask_passport contains WHEN is_account_group_member(hera-all-esg-read-unmasked) OR
    is_account_group_member(hera-all-admin-admin-unmasked) THEN v, and never mentions hera-all-esg-
    read-masked.
    """
    sql = dict(render_sql_objects({**_REF, "groups": _GROUPS}))["acm_mask_passport.sql"]
    assert ("RETURN CASE\n"
            "         WHEN is_account_group_member('hera-all-esg-read-unmasked')\n"
            "           OR is_account_group_member('hera-all-admin-admin-unmasked')\n"
            "         THEN v\n") in sql
    assert "hera-all-esg-read-masked" not in sql


def test_masks_fail_closed_without_unmasked_groups():
    """When no group name ends in "unmasked", every mask fails closed.

    Expected: all 4 masks render WHEN FALSE THEN v, so everyone sees masked values.
    """
    sql = dict(render_sql_objects({**_REF, "groups": [{"name": "only-masked", "external_id": None}]}))
    for name in ("acm_mask_email.sql", "acm_mask_full_name.sql", "acm_mask_identifier.sql", "acm_mask_passport.sql"):
        assert "WHEN FALSE\n         THEN v" in sql[name], name       # nobody unmasked


def test_group_name_quote_is_escaped():
    """A single quote in a group name is escaped in the generated SQL string literal.

    Expected: is_account_group_member('o''brien-unmasked').
    """
    cond = sql_variables({**_REF, "groups": [{"name": "o'brien-unmasked"}]})["unmasked_condition"]
    assert cond == "is_account_group_member('o''brien-unmasked')"


def test_multiline_placeholder_never_in_a_comment():
    """The multi-line ${unmasked_condition} placeholder appears only on code lines in the packaged
    SQL, never inside a -- comment (where its later lines would escape the comment).

    Expected: no packaged .sql file has that placeholder on a commented line.
    """
    # unmasked_condition spans several lines; substituted inside a `--` comment, its 2nd+ lines would
    # escape the comment and break the SQL. It may only appear on code lines.
    for f in resources.files("eastspring_uc_governance").joinpath("sql").iterdir():
        if f.name.endswith(".sql"):
            for line in f.read_text().splitlines():
                if "${unmasked_condition}" in line:
                    assert not line.lstrip().startswith("--") and "--" not in line.split("${unmasked_condition}")[0], (f.name, line)


def test_esg_fixture_reference_unmasked_groups():
    """The ESG fixture reference yields the expected unmasked groups.

    Expected: [hera-all-esg-read-unmasked, hera-all-admin-admin-unmasked].
    """
    ref = yaml.safe_load(FIXTURE_REFERENCE.read_text())
    assert unmasked_groups(ref) == ["hera-all-esg-read-unmasked", "hera-all-admin-admin-unmasked"]
