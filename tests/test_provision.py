"""Tests for CLI provisioning: the pure planners, and the runner with a mocked subprocess."""
from __future__ import annotations

import json
import subprocess

import pytest

from framework.provision import cli_runner, planner
from framework.provision.planner import CONFLICT, CREATE, EXISTS, RESOLVE

GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"

_ACCOUNT = {"groups": [{"name": "local-grp", "external_id": None},
                       {"name": "aim-grp", "external_id": GUID}]}

_REF = {
    "target_catalog": "testcat",
    "source_schemas": ["testcat.gold"],                          # never provisioned
    "governance_schemas": ["testcat.gov"],
    "serving_schemas": ["testcat.serving", "othercat.serving"],  # othercat: not under target_catalog
}


def _by_target(actions):
    return {a.target: a for a in actions}


# ---- planner: groups (account level) ---------------------------------------

def test_local_group_create_then_exists():
    a = _by_target(planner.plan_groups(_ACCOUNT, []))["local-grp"]
    assert a.status == CREATE
    assert a.argv == ("account", "groups", "create", "--display-name", "local-grp")
    a = _by_target(planner.plan_groups(_ACCOUNT, [{"displayName": "local-grp", "id": "1"}]))["local-grp"]
    assert a.status == EXISTS and a.argv == ()


def test_aim_group_resolve_when_absent():
    a = _by_target(planner.plan_groups(_ACCOUNT, []))["aim-grp"]
    assert a.status == RESOLVE and a.argv == ("account", "iam-v2", "resolve-group", GUID)


def test_aim_group_exists_when_external_id_already_provisioned():
    live = [{"displayName": "aim-grp", "externalId": GUID.upper()}]   # GUID compare is case-insensitive
    assert _by_target(planner.plan_groups(_ACCOUNT, live))["aim-grp"].status == EXISTS


def test_aim_group_conflicts():
    # same name exists without the link (e.g. a local group) -> don't resolve, a human must act
    a = _by_target(planner.plan_groups(_ACCOUNT, [{"displayName": "aim-grp"}]))["aim-grp"]
    assert a.status == CONFLICT and a.argv == () and "delete it first" in a.note
    # the GUID is already provisioned under another name -> wrong GUID or wrong name
    a = _by_target(planner.plan_groups(_ACCOUNT, [{"displayName": "other", "externalId": GUID}]))["aim-grp"]
    assert a.status == CONFLICT and "'other'" in a.note


def test_desired_groups_skips_unnamed_and_duplicates():
    account = {"groups": [{"name": "g1", "external_id": None}, {"name": "g1", "external_id": GUID},
                          {"external_id": None}]}
    assert planner.desired_groups(account) == [("g1", None)]


# ---- planner: schemas (per env) --------------------------------------------

def test_desired_schemas_governance_and_serving_under_target_catalog_only():
    assert planner.desired_schemas(_REF) == ["testcat.gov", "testcat.serving"]


def test_schema_exists_vs_create():
    acts = _by_target(planner.plan_schemas(_REF, {"testcat.gov"}, source="config/reference/x.yaml"))
    assert acts["testcat.gov"].status == EXISTS and acts["testcat.gov"].argv == ()
    create = acts["testcat.serving"]
    assert create.status == CREATE
    assert create.argv == ("schemas", "create", "serving", "testcat",
                           "--comment", "Provisioned from config/reference/x.yaml")


# ---- runner (mocked subprocess) --------------------------------------------

class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def calls(monkeypatch):
    """Record CLI invocations; each test sets ``calls.reply`` (cmd -> _Proc)."""
    recorded = type("Calls", (list,), {})()

    def fake_run(cmd, capture_output, text):
        recorded.append(cmd)
        return recorded.reply(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return recorded


def test_plan_mode_runs_nothing(calls):
    calls.reply = lambda cmd: pytest.fail(f"plan mode ran {cmd}")
    res = cli_runner.execute(planner.plan_groups(_ACCOUNT, []), dry_run=True)
    assert [r["status"] for r in res] == ["PLANNED", "PLANNED"] and calls == []


def test_apply_runs_with_profile_and_json_output(calls):
    calls.reply = lambda cmd: _Proc(stdout=json.dumps({"group": {"group_name": "aim-grp"}})
                                    if "resolve-group" in cmd else "{}")
    res = cli_runner.execute(planner.plan_groups(_ACCOUNT, []), profile="ACCT", dry_run=False)
    assert [r["status"] for r in res] == ["SUCCEEDED", "SUCCEEDED"]
    assert all(cmd[0] == "databricks" and cmd[-4:] == ["-o", "json", "--profile", "ACCT"]
               for cmd in calls)


def test_schema_already_exists_is_treated_as_exists(calls):
    calls.reply = lambda cmd: _Proc(1, stderr="Error: Schema 'serving' already exists")
    creates = [a for a in planner.plan_schemas(_REF, {"testcat.gov"}) if a.status == CREATE]
    assert cli_runner.execute(creates, dry_run=False)[0]["status"] == EXISTS


def test_resolve_name_mismatch_fails_loudly(calls):
    calls.reply = lambda cmd: _Proc(stdout=json.dumps({"group": {"group_name": "someone-else"}}))
    resolve = [a for a in planner.plan_groups(_ACCOUNT, []) if a.status == RESOLVE]
    res = cli_runner.execute(resolve, dry_run=False)
    assert res[0]["status"] == "FAILED" and "IS now provisioned" in res[0]["error"]
    assert cli_runner.failed(res)


def test_cli_error_is_reported_and_run_continues(calls):
    calls.reply = lambda cmd: _Proc(1, stderr="PERMISSION_DENIED") if "groups" in cmd else \
        _Proc(stdout=json.dumps({"group": {"group_name": "aim-grp"}}))
    res = cli_runner.execute(planner.plan_groups(_ACCOUNT, []), dry_run=False)
    assert [r["status"] for r in res] == ["FAILED", "SUCCEEDED"]
    assert "PERMISSION_DENIED" in res[0]["error"]


def test_conflict_fails_the_run_without_executing(calls):
    calls.reply = lambda cmd: pytest.fail(f"conflict ran {cmd}")
    res = cli_runner.execute(planner.plan_groups(_ACCOUNT, [{"displayName": "aim-grp"}])[1:],
                             dry_run=False)
    assert res[0]["status"] == CONFLICT and cli_runner.failed(res)
