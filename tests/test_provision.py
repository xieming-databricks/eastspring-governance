"""Tests for CLI provisioning: the pure planners, and the runner with a mocked subprocess."""
from __future__ import annotations

import json
import subprocess

import pytest

from eastspring_uc_governance.provision import cli_runner, planner
from eastspring_uc_governance.provision.planner import CONFLICT, CREATE, EXISTS, RESOLVE, UPDATE

GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"

_GROUPS_REF = {"groups": [{"name": "local-grp", "external_id": None},
                       {"name": "aim-grp", "external_id": GUID}]}

_REF = {
    "target_catalog": "testcat",
    "source_schemas": ["testcat.gold"],                          # never provisioned
    "governance_schemas": ["testcat.gov"],
    "serving_schemas": ["testcat.serving", "othercat.serving"],  # othercat: not under target_catalog
}

_TAGS_REF = {"governed_tags": {"acm_pii_type": ["FULL_NAME", "EMAIL", "secret"],
                               "acm_free": None}}                # null: any value allowed


def _policy(key, *values):
    """A live tag policy as list-tag-policies returns it (no values -> the key has no "values")."""
    return {"tag_key": key, "id": f"id-{key}", **({"values": [{"name": v} for v in values]} if values else {})}


def _by_target(actions):
    return {a.target: a for a in actions}


# ---- planner: groups (reference `groups`, via the workspace proxy API) -------

def test_local_group_create_then_exists():
    """A group with external_id null is planned as a local group: created when it is absent from the
    account, left alone when it already exists.

    Expected: CREATE with argv workspace-iam-v2 create-group-proxy --group-name local-grp when
    absent; EXISTS with no command once a group of that name is live.
    """
    a = _by_target(planner.plan_groups(_GROUPS_REF, []))["local-grp"]
    assert a.status == CREATE
    assert a.argv == ("workspace-iam-v2", "create-group-proxy", "--group-name", "local-grp")
    a = _by_target(planner.plan_groups(_GROUPS_REF, [{"group_name": "local-grp", "group_id": "1"}]))["local-grp"]
    assert a.status == EXISTS and a.argv == ()


def test_aim_group_resolve_when_absent():
    """A group with an external_id that isn't in the account yet is planned as an AIM resolve by its
    Entra Object ID.

    Expected: RESOLVE with argv workspace-iam-v2 resolve-group-proxy <GUID>.
    """
    a = _by_target(planner.plan_groups(_GROUPS_REF, []))["aim-grp"]
    assert a.status == RESOLVE and a.argv == ("workspace-iam-v2", "resolve-group-proxy", GUID)


def test_aim_group_exists_when_external_id_already_provisioned():
    """An AIM group already provisioned with the same external_id and name is left alone; the GUID
    comparison ignores case.

    Expected: EXISTS, even though the live external_id is upper-case.
    """
    live = [{"group_name": "aim-grp", "external_id": GUID.upper()}]   # GUID compare is case-insensitive
    assert _by_target(planner.plan_groups(_GROUPS_REF, live))["aim-grp"].status == EXISTS


def test_aim_group_conflicts():
    """Two unsafe AIM cases are refused rather than resolved: a same-named group exists without that
    external_id, or the external_id is already provisioned under another name.

    Expected: CONFLICT with no command in both cases; the notes say "delete it first" and name the
    other group ('other').
    """
    # same name exists without the link (e.g. a local group) -> don't resolve, a human must act
    a = _by_target(planner.plan_groups(_GROUPS_REF, [{"group_name": "aim-grp"}]))["aim-grp"]
    assert a.status == CONFLICT and a.argv == () and "delete it first" in a.note
    # the GUID is already provisioned under another name -> wrong GUID or wrong name
    a = _by_target(planner.plan_groups(_GROUPS_REF, [{"group_name": "other", "external_id": GUID}]))["aim-grp"]
    assert a.status == CONFLICT and "'other'" in a.note


def test_desired_groups_skips_unnamed_and_duplicates():
    """Reading groups from the reference skips entries without a name and keeps only the first entry
    for a repeated name.

    Expected: [(g1, None)].
    """
    account = {"groups": [{"name": "g1", "external_id": None}, {"name": "g1", "external_id": GUID},
                          {"external_id": None}]}
    assert planner.desired_groups(account) == [("g1", None)]


# ---- planner: schemas (per env) --------------------------------------------

def test_desired_schemas_governance_and_serving_under_target_catalog_only():
    """The schemas to provision are the governance and serving schemas under target_catalog; source
    schemas and other catalogs are excluded.

    Expected: [testcat.gov, testcat.serving].
    """
    assert planner.desired_schemas(_REF) == ["testcat.gov", "testcat.serving"]


def test_schema_exists_vs_create():
    """A schema that is already live is left alone, and a missing one is planned with a databricks
    schemas create command.

    Expected: testcat.gov is EXISTS with no command; testcat.serving is CREATE with argv schemas
    create serving testcat --comment "Provisioned from config/reference/x.yaml".
    """
    acts = _by_target(planner.plan_schemas(_REF, {"testcat.gov"}, source="config/reference/x.yaml"))
    assert acts["testcat.gov"].status == EXISTS and acts["testcat.gov"].argv == ()
    create = acts["testcat.serving"]
    assert create.status == CREATE
    assert create.argv == ("schemas", "create", "serving", "testcat",
                           "--comment", "Provisioned from config/reference/x.yaml")


# ---- planner: governed tags (reference `governed_tags`, as tag policies) ------

def test_desired_governed_tags_keeps_order_dedupes_values_and_null():
    """governed_tags is read in order; repeated values are dropped; a null list stays None.

    Expected: [("acm_pii_type", ["FULL_NAME", "EMAIL"]), ("acm_free", None)]; no governed_tags -> [].
    """
    ref = {"governed_tags": {"acm_pii_type": ["FULL_NAME", "EMAIL", "FULL_NAME"], "acm_free": None}}
    assert planner.desired_governed_tags(ref) == [("acm_pii_type", ["FULL_NAME", "EMAIL"]),
                                                  ("acm_free", None)]
    assert planner.desired_governed_tags({}) == []


def test_missing_governed_tag_is_created_with_its_values():
    """A governed tag that doesn't exist is created with the reference's allowed values; a null
    list creates it without values (any value allowed).

    Expected: both CREATE; create-tag-policy --json with tag_key + description inside the JSON (the
    CLI rejects a positional key with --json), values only for acm_pii_type.
    """
    acts = _by_target(planner.plan_governed_tags(_TAGS_REF, [], source="reference/x.yaml"))
    pii, free = acts["acm_pii_type"], acts["acm_free"]
    assert pii.status == free.status == CREATE
    assert pii.argv[:3] == ("tag-policies", "create-tag-policy", "--json")
    assert json.loads(pii.argv[3]) == {"tag_key": "acm_pii_type",
                                       "description": "Provisioned from reference/x.yaml",
                                       "values": [{"name": "FULL_NAME"}, {"name": "EMAIL"},
                                                  {"name": "secret"}]}
    assert "values" not in json.loads(free.argv[3])


def test_governed_tag_missing_values_are_added_and_live_values_kept():
    """A live governed tag that lacks some reference values gets them added; values that are only
    live are kept (the update sends live + missing) and reported, never removed.

    Expected: UPDATE with update-tag-policy acm_pii_type values --json, values in live order
    followed by the missing one; the note names what is added and what is left as is.
    """
    live = [_policy("acm_pii_type", "FULL_NAME", "PASSPORT", "EMAIL"), _policy("acm_free")]
    acts = _by_target(planner.plan_governed_tags(_TAGS_REF, live))
    upd = acts["acm_pii_type"]
    assert upd.status == UPDATE
    assert upd.argv[:5] == ("tag-policies", "update-tag-policy", "acm_pii_type", "values", "--json")
    assert [v["name"] for v in json.loads(upd.argv[5])["values"]] == ["FULL_NAME", "PASSPORT",
                                                                     "EMAIL", "secret"]
    assert "add: secret" in upd.note and "PASSPORT" in upd.note


def test_governed_tag_is_never_narrowed_or_shrunk():
    """Nothing that would restrict a live governed tag is planned: a tag accepting any value is not
    narrowed to the reference's list, and a reference that lists fewer values removes none.

    Expected: all EXISTS with no command; the notes explain what was left as is.
    """
    ref = {"governed_tags": {"acm_any": ["A"], "acm_more": ["A"], "acm_null": None, "acm_same": ["A", "B"]}}
    live = [_policy("acm_any"), _policy("acm_more", "A", "B"), _policy("acm_null", "A"),
            _policy("acm_same", "B", "A")]
    acts = _by_target(planner.plan_governed_tags(ref, live))
    assert {k: (a.status, a.argv) for k, a in acts.items()} == {k: (EXISTS, ()) for k in acts}
    assert "not narrowed" in acts["acm_any"].note
    assert "B — left as is" in acts["acm_more"].note
    assert "only: A" in acts["acm_null"].note
    assert acts["acm_same"].note == ""


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
    """Plan (dry-run) mode never calls the CLI.

    Expected: both group actions are PLANNED and no subprocess was run.
    """
    calls.reply = lambda cmd: pytest.fail(f"plan mode ran {cmd}")
    res = cli_runner.execute(planner.plan_groups(_GROUPS_REF, []), dry_run=True)
    assert [r["status"] for r in res] == ["PLANNED", "PLANNED"] and calls == []


def test_apply_runs_with_profile_and_json_output(calls):
    """Apply mode runs every planned command through the databricks CLI with JSON output and the
    given profile.

    Expected: both actions SUCCEEDED, and every call starts with databricks and ends with -o json
    --profile ACCT.
    """
    calls.reply = lambda cmd: _Proc(stdout=json.dumps({"group": {"group_name": "aim-grp"}})
                                    if "resolve-group-proxy" in cmd else "{}")
    res = cli_runner.execute(planner.plan_groups(_GROUPS_REF, []), profile="ACCT", dry_run=False)
    assert [r["status"] for r in res] == ["SUCCEEDED", "SUCCEEDED"]
    assert all(cmd[0] == "databricks" and cmd[-4:] == ["-o", "json", "--profile", "ACCT"]
               for cmd in calls)


def test_schema_already_exists_is_treated_as_exists(calls):
    """A schema create that fails with "already exists" (it existed but wasn't visible to the plan)
    counts as present, not as a failure.

    Expected: status EXISTS.
    """
    calls.reply = lambda cmd: _Proc(1, stderr="Error: Schema 'serving' already exists")
    creates = [a for a in planner.plan_schemas(_REF, {"testcat.gov"}) if a.status == CREATE]
    assert cli_runner.execute(creates, dry_run=False)[0]["status"] == EXISTS


def test_resolve_name_mismatch_fails_loudly(calls):
    """If resolve-group-proxy returns a group with a different name than the reference, the run
    fails loudly, since that group has already been provisioned.

    Expected: status FAILED, an error saying the group "IS now provisioned", and the run counts as
    failed.
    """
    calls.reply = lambda cmd: _Proc(stdout=json.dumps({"group": {"group_name": "someone-else"}}))
    resolve = [a for a in planner.plan_groups(_GROUPS_REF, []) if a.status == RESOLVE]
    res = cli_runner.execute(resolve, dry_run=False)
    assert res[0]["status"] == "FAILED" and "IS now provisioned" in res[0]["error"]
    assert cli_runner.failed(res)


def test_cli_error_is_reported_and_run_continues(calls):
    """A failing CLI command is reported and the remaining actions still run.

    Expected: statuses [FAILED, SUCCEEDED], with PERMISSION_DENIED in the first error.
    """
    calls.reply = lambda cmd: _Proc(1, stderr="PERMISSION_DENIED") if "create-group-proxy" in cmd else \
        _Proc(stdout=json.dumps({"group": {"group_name": "aim-grp"}}))
    res = cli_runner.execute(planner.plan_groups(_GROUPS_REF, []), dry_run=False)
    assert [r["status"] for r in res] == ["FAILED", "SUCCEEDED"]
    assert "PERMISSION_DENIED" in res[0]["error"]


def test_conflict_fails_the_run_without_executing(calls):
    """A CONFLICT action is never executed, but it still makes the run fail.

    Expected: status CONFLICT, no CLI call made, and failed() is True.
    """
    calls.reply = lambda cmd: pytest.fail(f"conflict ran {cmd}")
    res = cli_runner.execute(planner.plan_groups(_GROUPS_REF, [{"group_name": "aim-grp"}])[1:],
                             dry_run=False)
    assert res[0]["status"] == CONFLICT and cli_runner.failed(res)


# ---- no account-level access ------------------------------------------------

def test_no_command_uses_account_level_access(calls):
    """Listing, group commands and schema commands all stay workspace-level, because the customer
    grants no account access.

    Expected: no CLI call starts with databricks account, and groups are listed through workspace-
    iam-v2 list-groups-proxy.
    """
    # every group/schema command must be workspace-level (the customer grants no account access):
    # groups go through `workspace-iam-v2 *-proxy`, never `databricks account ...`.
    calls.reply = lambda cmd: _Proc(stdout=json.dumps({"group": {"group_name": "aim-grp"}})
                                    if "resolve-group-proxy" in cmd else "[]")
    cli_runner.list_account_groups("WS")
    cli_runner.list_schemas("testcat", "WS")
    cli_runner.execute(planner.plan_groups(_GROUPS_REF, []) + planner.plan_schemas(_REF, set()),
                       profile="WS", dry_run=False)
    assert calls and all(cmd[1] != "account" for cmd in calls), [c[:3] for c in calls]
    assert calls[0][1:3] == ["workspace-iam-v2", "list-groups-proxy"]


def test_governed_tag_update_is_executed_and_note_is_not_an_error(calls):
    """An UPDATE runs in apply mode like a CREATE; its note is information, not an error.

    Expected: SUCCEEDED, the update-tag-policy argv went to the CLI, error None, note kept.
    """
    calls.reply = lambda cmd: _Proc(stdout="{}")
    acts = planner.plan_governed_tags({"governed_tags": {"acm_pii_type": ["A", "B"]}},
                                      [_policy("acm_pii_type", "A")])
    res = cli_runner.execute(acts, dry_run=False)
    assert res[0]["status"] == "SUCCEEDED" and res[0]["error"] is None
    assert res[0]["note"] == "add: B" and not cli_runner.failed(res)
    assert calls[0][1:5] == ["tag-policies", "update-tag-policy", "acm_pii_type", "values"]


@pytest.mark.parametrize("resp", [{"group": {"group_name": "aim-grp"}},   # wrapped (as account resolve)
                                  {"group_name": "aim-grp"}])             # bare group object
def test_resolve_accepts_wrapped_or_bare_group(calls, resp):
    """For both response shapes resolve-group-proxy might return ({"group": {...}} or a bare group
    object), the returned name is read correctly.

    Expected: status SUCCEEDED for each shape.
    """
    calls.reply = lambda cmd: _Proc(stdout=json.dumps(resp))
    resolve = [a for a in planner.plan_groups(_GROUPS_REF, []) if a.status == RESOLVE]
    assert cli_runner.execute(resolve, dry_run=False)[0]["status"] == "SUCCEEDED"


# ---- cli: --reference is required and never guessed ---------------------

def test_cli_requires_reference(capsys):
    """ucgov-provision refuses to run without --reference (no --env / --config-root guessing).

    Expected: argparse exits with code 2 for both subcommands and the error names --reference.
    """
    from eastspring_uc_governance.provision.cli import main
    for sub in ("groups", "schemas", "tags"):
        with pytest.raises(SystemExit) as exc:
            main([sub])
        assert exc.value.code == 2
        assert "--reference" in capsys.readouterr().err


@pytest.mark.parametrize("content", [None, "groups: [unclosed\n", "- just\n- a list\n"],
                         ids=["missing", "unparseable", "not_a_mapping"])
def test_cli_bad_reference_fails_before_any_cli_call(tmp_path, calls, capsys, content):
    """A --reference file that is missing, isn't valid YAML, or isn't a mapping stops provisioning
    before the databricks CLI is called.

    Expected: exit code 1, a "reference allow-list" message, and no CLI invocation.
    """
    from eastspring_uc_governance.provision.cli import main
    ref = tmp_path / "esg.yaml"
    if content is not None:
        ref.write_text(content)
    assert main(["schemas", "--reference", str(ref)]) == 1
    assert "reference allow-list" in capsys.readouterr().out
    assert calls == []


def test_cli_plans_from_the_given_reference(tmp_path, calls, capsys):
    """ucgov-provision reads exactly the --reference file it is given.

    Expected: exit code 0; schemas are listed in that file's target_catalog, and its governance +
    serving schemas are planned.
    """
    import yaml
    from eastspring_uc_governance.provision.cli import main
    ref = tmp_path / "anywhere" / "custom-name.yaml"
    ref.parent.mkdir()
    ref.write_text(yaml.safe_dump(_REF))
    calls.reply = lambda cmd: _Proc(stdout="[]")
    assert main(["schemas", "--reference", str(ref)]) == 0
    assert calls[0][1:4] == ["schemas", "list", "testcat"]
    out = capsys.readouterr().out
    assert "testcat.gov" in out and "testcat.serving" in out


def test_cli_tags_plans_from_the_given_reference(tmp_path, calls, capsys):
    """ucgov-provision tags lists the governed tags once and plans the reference's governed_tags,
    printing what would be added and what is left as is.

    Expected: exit code 0; one call, tag-policies list-tag-policies; acm_pii_type PLANNED with the
    add note; acm_free PLANNED (missing).
    """
    import yaml
    from eastspring_uc_governance.provision.cli import main
    ref = tmp_path / "esg.yaml"
    ref.write_text(yaml.safe_dump(_TAGS_REF))
    calls.reply = lambda cmd: _Proc(stdout=json.dumps([_policy("acm_pii_type", "FULL_NAME", "EMAIL")]))
    assert main(["tags", "--reference", str(ref)]) == 0
    assert len(calls) == 1 and calls[0][1:3] == ["tag-policies", "list-tag-policies"]
    out = capsys.readouterr().out
    assert "[tags] acm_pii_type" in out and "add: secret" in out and "acm_free" in out
    assert "2 item(s): {'PLANNED': 2}" in out


def test_cli_tags_without_governed_tags_is_a_no_op(tmp_path, calls, capsys):
    """A reference without governed_tags has nothing to provision: no CLI call, exit code 0."""
    from eastspring_uc_governance.provision.cli import main
    ref = tmp_path / "esg.yaml"
    ref.write_text("target_catalog: testcat\n")
    assert main(["tags", "--reference", str(ref)]) == 0
    assert calls == [] and "nothing to provision" in capsys.readouterr().out
