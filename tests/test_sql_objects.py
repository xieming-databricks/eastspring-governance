"""Tests for rendering the packaged SQL objects (catalog / governance schema are placeholders)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.sql_objects import render_sql_objects, sql_variables

REPO = Path(__file__).resolve().parents[1]
_REF = {"target_catalog": "testcat", "governance_schemas": ["testcat.gov"]}


def test_sql_variables_from_reference():
    assert sql_variables(_REF) == {"catalog": "testcat", "governance_schema": "gov"}


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
