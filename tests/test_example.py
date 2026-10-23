"""examples/esg must stay a complete, working deployment: its config is what its workbook generates,
it validates cleanly, and everything it refers to is created by the example itself — the dev
seeds, provisioning (the reference's schemas) or its SQL folder (the mask functions)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.compile import compile_config
from eastspring_uc_governance.generate import cli as generate_cli
from eastspring_uc_governance.sql_objects import render_sql_objects
from eastspring_uc_governance.validation import validate_dir

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "esg"
WORKBOOK = EXAMPLE / "excels" / "esg" / "access-matrix-esg_workshop-v3-dynamic-views.xlsx"
CONFIG = EXAMPLE / "config" / "esg"
REFERENCE = EXAMPLE / "reference" / "esg.yaml"
SQL = EXAMPLE / "sql" / "esg"
SEEDS = [EXAMPLE / "dev-tools" / "seed_esg.sql", EXAMPLE / "dev-tools" / "seed_esg_rls.sql"]


def _plain(fqn: str) -> str:
    """`a`.`b`.c -> a.b.c"""
    return ".".join(seg.strip("`") for seg in re.findall(r"`[^`]+`|[^.]+", fqn.strip()))


def _seeded() -> tuple[set[str], set[str], dict[str, set[str]], str]:
    """(catalogs, schemas, {table: columns}, all seed SQL without comments) from the seed scripts."""
    sql = "\n".join(re.sub(r"--[^\n]*", "", f.read_text()) for f in SEEDS)
    catalogs = {_plain(m) for m in re.findall(r"CREATE\s+CATALOG\s+IF\s+NOT\s+EXISTS\s+([^\s;]+)", sql, re.I)}
    schemas = {_plain(m) for m in re.findall(r"CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+([^\s;]+)", sql, re.I)}
    tables = {_plain(name): {c.split()[0].strip("`").lower() for c in body.split(",") if c.strip()}
              for name, body in re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([^\s(]+)\s*\(([^)]*)\)",
                                           sql, re.I)}
    return catalogs, schemas, tables, sql


@pytest.fixture(scope="module")
def example():
    docs = {f.stem: yaml.safe_load(f.read_text()) for f in CONFIG.glob("*.yaml")}
    reference = yaml.safe_load(REFERENCE.read_text())
    return docs, reference


def test_example_config_is_what_its_workbook_generates(capsys):
    """config/esg is exactly what the example workbook generates — a workbook edited (and saved)
    without regenerating the config fails here.

    Expected: ucgov-generate --verify exits 0 ("is up to date").
    """
    rc = generate_cli.main([str(WORKBOOK), str(CONFIG), "--verify"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "is up to date" in out


def test_example_validates_without_errors_or_warnings():
    """The example config validates against its reference and SQL folder with no issue at all
    (warnings fail the CLI by default, so they count here too).

    Expected: no errors, no warnings; the SQL folder check ran and is ok.
    """
    result = validate_dir(CONFIG, reference=REFERENCE, sql_dir=SQL)
    assert result.issues == [], [str(i) for i in result.sorted_issues()]
    assert {c.target: c.status for c in result.checks}["sql"] == "ok"


def test_example_reference_carries_no_entra_ids(example):
    """Every group in the example reference is a local group (external_id null): the example runs in
    any dev workspace and never holds a real tenant's Entra object IDs.

    Expected: at least one group, all with external_id None.
    """
    _, reference = example
    groups = reference.get("groups") or []
    assert groups and all(g.get("external_id") is None for g in groups), groups


def test_everything_the_config_refers_to_is_created_by_the_example(example):
    """Every object the compiled config refers to (label / tag / grant / policy targets, mask
    functions) is created by the example: tables, columns and source schemas by the seeds, the
    governance + serving schemas by `ucgov-provision schemas`, the mask functions by its SQL folder.

    Expected: no referenced object is missing.
    """
    docs, reference = example
    catalogs, schemas, tables, _ = _seeded()
    provisioned = set(reference.get("governance_schemas") or []) | set(reference.get("serving_schemas") or [])
    functions = {_plain(m) for _, sql in render_sql_objects(reference, SQL)
                 for m in re.findall(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+(\S+?)\s*\(", sql, re.I)}
    state = compile_config(docs, mask_exempt_principals=reference.get("mask_exempt_principals") or [])
    assert state.ok, state.errors

    def created(ref) -> bool:
        if ref.object_type == "catalog":
            return ref.fqn in catalogs
        if ref.object_type == "schema":
            return ref.fqn in schemas | provisioned
        if ref.object_type in ("table", "view"):
            return ref.fqn in tables
        if ref.object_type == "column":
            table, col = ref.fqn.rsplit(".", 1)
            return col.lower() in tables.get(table, set())
        if ref.object_type == "function":
            return ref.fqn in functions
        return False

    missing = sorted({(r.object_type, r.fqn, r.origin) for r in state.refs if not created(r)})
    assert state.refs and missing == []


def test_views_read_seeded_tables_and_rls_columns(example):
    """Every dynamic view's source is a seeded table, and each RLS view's rls_column is a column of
    its source (the semi-join filters on it).

    Expected: no view with a missing source or rls_column; at least one RLS view.
    """
    docs, _ = example
    _, _, tables, _ = _seeded()
    views = docs["rbac_views"]["views"]
    problems = [v["view"] for v in views
                if v["source"] not in tables
                or (v.get("rls_column") and v["rls_column"].lower() not in tables[v["source"]])]
    assert problems == []
    assert any(v.get("rls_column") for v in views)


def test_rls_allow_list_inputs_are_seeded_with_reference_groups(example):
    """The RLS MV reads only seeded tables, and every group in the seeded entitlements is a reference
    group — a stale (e.g. renamed) group name would silently grant nothing.

    Expected: the MV's FROM / JOIN tables are seeded; the entitlement groups are reference groups.
    """
    _, reference = example
    _, _, tables, seed_sql = _seeded()
    mv = dict(render_sql_objects(reference, SQL))["group_portfolio_access.sql"]
    inputs = {_plain(m) for m in re.findall(r"(?:FROM|JOIN)\s+(`[^`]+`\.`[^`]+`\.\w+)", mv)}
    assert inputs and inputs <= set(tables), inputs - set(tables)

    rows = re.search(r"INSERT\s+INTO\s+\S+\.entitlements\s+VALUES(.*?);", seed_sql, re.I | re.S).group(1)
    entitled = set(re.findall(r"\(\s*'([^']+)'", rows))
    known = {g["name"] for g in reference["groups"]}
    assert entitled and entitled <= known, entitled - known
