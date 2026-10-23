"""Tests for the SQL-folder checks (validation.sql_check): a deployment's mask UDFs + RLS MV
against its reference + config. Each corruption is made on a tmp copy of the fixture folder."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from eastspring_uc_governance.validation import validate_dir
from eastspring_uc_governance.validation.sql_check import check_sql_dir

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ESG = FIXTURES / "config" / "esg"
REF = yaml.safe_load((FIXTURES / "config" / "reference" / "esg.yaml").read_text())
SQL = FIXTURES / "sql" / "esg"


def _docs() -> dict:
    return {f.name: yaml.safe_load(f.read_text()) for f in ESG.glob("*.yaml")}


def _sql_copy(tmp_path: Path) -> Path:
    d = tmp_path / "esg"
    shutil.copytree(SQL, d)
    return d


def _messages(issues, severity=None):
    return [f"{i.file}: {i.message}" for i in issues if severity is None or i.severity == severity]


def test_fixture_sql_folder_is_clean():
    """The fixture SQL folder passes every check against the fixture reference + config.

    Expected: no issues; one ok check naming 5 files, 4 masks, 4 masking rules and the RLS table.
    """
    issues, checks = check_sql_dir(SQL, REF, _docs())
    assert issues == []
    assert checks[0].status == "ok" and "5 file(s), 4 mask function(s)" in checks[0].detail
    assert "hera-dev.governance.group_portfolio_access for 4 RLS view(s)" in checks[0].detail


def test_mask_without_unmasked_condition_is_an_error(tmp_path):
    """A mask that hard-codes who sees raw values (instead of ${unmasked_condition}) is refused.

    Expected: one error naming acm_mask_email.sql and ${unmasked_condition}.
    """
    d = _sql_copy(tmp_path)
    f = d / "acm_mask_email.sql"
    f.write_text(f.read_text().replace("${unmasked_condition}", "is_account_group_member('someone')"))
    issues, _ = check_sql_dir(d, REF, _docs())
    assert _messages(issues) == ["acm_mask_email.sql: does not use ${unmasked_condition}: who reads raw "
                                 "values must come from the reference's *unmasked groups"]


def test_mask_must_define_the_function_named_like_the_file(tmp_path):
    """acm_mask_<x>.sql must define `${catalog}`.`${governance_schema}`.acm_mask_<x>; a different
    name (or a hard-coded catalog) is refused — the Masking Rules point at the file's name.

    Expected: an error for the wrong name; another for the hard-coded catalog.
    """
    d = _sql_copy(tmp_path)
    f = d / "acm_mask_email.sql"
    f.write_text(f.read_text().replace(".acm_mask_email(", ".acm_mask_mail("))
    g = d / "acm_mask_passport.sql"
    g.write_text(g.read_text().replace("`${catalog}`.`${governance_schema}`.acm_mask_passport",
                                       "`hera-dev`.`governance`.acm_mask_passport"))
    msgs = _messages(check_sql_dir(d, REF, _docs())[0])
    assert any(m.startswith("acm_mask_email.sql: must define") and "found: acm_mask_mail" in m for m in msgs)
    assert any(m.startswith("acm_mask_passport.sql: must define") and "found: none" in m for m in msgs)


def test_unknown_placeholder_is_an_error(tmp_path):
    """A file with an unknown placeholder doesn't render — reported against that file.

    Expected: one error for group_portfolio_access.sql mentioning governance_shema.
    """
    d = _sql_copy(tmp_path)
    f = d / "group_portfolio_access.sql"
    f.write_text(f.read_text().replace("`${governance_schema}`.entitlements e", "`${governance_shema}`.entitlements e"))
    msgs = _messages(check_sql_dir(d, REF, _docs())[0], "error")
    assert len(msgs) == 1 and msgs[0].startswith("group_portfolio_access.sql: does not render") \
        and "governance_shema" in msgs[0]


def test_placeholder_inside_a_comment_is_an_error(tmp_path):
    """${unmasked_condition} after `--` on a line would push its later lines out of the comment.

    Expected: an error for acm_mask_full_name.sql about the comment.
    """
    d = _sql_copy(tmp_path)
    f = d / "acm_mask_full_name.sql"
    f.write_text("-- unmasked: ${unmasked_condition}\n" + f.read_text())
    assert any("acm_mask_full_name.sql: ${unmasked_condition} appears after `--`" in m
               for m in _messages(check_sql_dir(d, REF, _docs())[0]))


def test_masking_rule_function_needs_its_mask_file(tmp_path):
    """Every Masking Rules function (policies.yaml) must have its mask file — otherwise the ABAC
    policy fails at apply.

    Expected: an error at policies.yaml column_mask_policies/<i>/function naming acm_mask_passport.sql.
    """
    d = _sql_copy(tmp_path)
    (d / "acm_mask_passport.sql").unlink()
    issues, _ = check_sql_dir(d, REF, _docs())
    hits = [i for i in issues if i.file == "policies.yaml"]
    assert len(hits) == 1 and hits[0].path.endswith("/function")
    assert "has no file in esg/ (expected acm_mask_passport.sql" in hits[0].message


def test_rls_table_not_created_by_the_sql_is_a_warning_with_a_hint(tmp_path):
    """If the reference's rls_entitlement_table isn't created by any file (e.g. the governance
    schema was renamed in one place only), it's a warning — it may be a customer table — and the
    hint names what the SQL does create.

    Expected: one warning on reference rls_entitlement_table, with "wrong schema?" and the real FQN.
    """
    ref = {**REF, "rls_entitlement_table": "hera-dev.other.group_portfolio_access"}
    issues, checks = check_sql_dir(SQL, ref, _docs())
    assert [(i.file, i.path, i.severity) for i in issues] == [("reference", "rls_entitlement_table", "warning")]
    assert "creates hera-dev.governance.group_portfolio_access — wrong schema?" in issues[0].message
    assert checks[0].status == "warnings"


def test_rls_views_without_rls_table_is_an_error():
    """Views with an rls_column but no rls_entitlement_table in the reference can't be built.

    Expected: one error on rbac_views.yaml.
    """
    ref = {k: v for k, v in REF.items() if k != "rls_entitlement_table"}
    issues, _ = check_sql_dir(SQL, ref, _docs())
    assert [(i.file, i.severity) for i in issues] == [("rbac_views.yaml", "error")]
    assert "set rls_column but the reference has no rls_entitlement_table" in issues[0].message


def test_reference_that_cannot_render_is_an_error():
    """Without a target_catalog nothing can be rendered — reported once, not per file.

    Expected: a single error on the reference.
    """
    issues, _ = check_sql_dir(SQL, {"groups": []}, _docs())
    assert _messages(issues) == ["reference: cannot render the SQL: reference has no target_catalog"]


def test_missing_sql_folder_is_an_error(tmp_path):
    """A SQL folder that doesn't exist is an error (never a silent skip).

    Expected: one error saying the folder was not found; check status missing.
    """
    issues, checks = check_sql_dir(tmp_path / "nope", REF, _docs())
    assert len(issues) == 1 and "SQL folder not found" in issues[0].message
    assert checks[0].status == "missing"


def test_validate_dir_runs_the_sql_check_only_when_given():
    """validate_dir checks the SQL folder when sql_dir is given; without it the check is recorded as
    skipped (the CLI makes --sql required).

    Expected: an ok "sql" check with sql_dir; a skipped one without.
    """
    with_sql = {c.target: c for c in validate_dir(ESG, reference=REF, sql_dir=SQL).checks}
    without = {c.target: c for c in validate_dir(ESG, reference=REF).checks}
    assert with_sql["sql"].status == "ok"
    assert without["sql"].status == "skipped"


@pytest.mark.parametrize("fn", ["hera-dev.governance.acm_mask_email",
                                "`hera-dev`.`governance`.`acm_mask_email`"])
def test_masking_rule_function_may_be_quoted(fn):
    """A Masking Rules function written with backticks matches its file like the plain form.

    Expected: no issues for either spelling.
    """
    docs = _docs()
    docs["policies.yaml"] = {"schema_version": "1.0",
                             "column_mask_policies": [{"scope": "hera-dev.gold", "function": fn,
                                                       "column_matching_tags": {"acm_pii_type": "EMAIL"}}]}
    assert check_sql_dir(SQL, REF, docs)[0] == []
