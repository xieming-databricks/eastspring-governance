"""Tests for apply DDL generation (pure string generation, no Spark)."""
from __future__ import annotations

import pytest

from eastspring_uc_governance.apply import statement


def _g(change_type, principal, priv, stype, fqn):
    return {"principal": principal, "privilege_type": priv, "securable_type": stype,
            "securable_fqn": fqn, "change_type": change_type}


def _t(change_type, stype, fqn, key, value=None):
    return {"securable_type": stype, "securable_fqn": fqn, "tag_key": key,
            "tag_value": value, "change_type": change_type}


# ---- grants: ADD -> GRANT, REMOVE -> REVOKE, all securable kinds --------

def test_grant_add_table():
    """An ADD grant on a table becomes a GRANT statement with backtick-quoted names.

    Expected: exactly GRANT SELECT ON TABLE `asset_prod`.`gold`.`investment_accounts` TO `hera-adbk-
    investments-read`.
    """
    assert statement.grant_statement(_g("ADD", "hera-adbk-investments-read", "SELECT", "table",
                                        "asset_prod.gold.investment_accounts")) == (
        "GRANT SELECT ON TABLE `asset_prod`.`gold`.`investment_accounts` "
        "TO `hera-adbk-investments-read`")


def test_grant_remove_table():
    """A REMOVE grant becomes a REVOKE statement.

    Expected: exactly REVOKE SELECT ON TABLE `c`.`s`.`t` FROM `analyst`.
    """
    assert statement.grant_statement(_g("REMOVE", "analyst", "SELECT", "table", "c.s.t")) == (
        "REVOKE SELECT ON TABLE `c`.`s`.`t` FROM `analyst`")


def test_grant_securable_keywords():
    """Each securable type gets its own ON keyword in GRANT statements.

    Expected: ON CATALOG `asset_prod`, ON SCHEMA `asset_prod`.`gold`, ON FUNCTION `c`.`s`.`f` and ON
    VOLUME `c`.`s`.`v`.
    """
    assert "ON CATALOG `asset_prod`" in statement.grant_statement(
        _g("ADD", "g", "USE_CATALOG", "catalog", "asset_prod"))
    assert "ON SCHEMA `asset_prod`.`gold`" in statement.grant_statement(
        _g("ADD", "g", "USE_SCHEMA", "schema", "asset_prod.gold"))
    assert "ON FUNCTION `c`.`s`.`f`" in statement.grant_statement(
        _g("ADD", "g", "EXECUTE", "function", "c.s.f"))
    assert "ON VOLUME `c`.`s`.`v`" in statement.grant_statement(
        _g("ADD", "g", "READ_VOLUME", "volume", "c.s.v"))


# ---- privilege form: underscore passthrough (UC accepts it directly) ----

@pytest.mark.parametrize("priv", [
    "SELECT", "MODIFY", "EXECUTE", "MANAGE", "USE_CATALOG", "USE_SCHEMA",
    "READ_VOLUME", "CREATE_TABLE", "CREATE_FUNCTION", "CREATE_VOLUME",
])
def test_privilege_passthrough_underscore_form(priv):
    """For each privilege in its underscore form (SELECT, MODIFY, USE_CATALOG, CREATE_TABLE, …), the
    name goes into the GRANT unchanged, since UC accepts that form.

    Expected: GRANT <privilege> ON CATALOG `c` TO `g`, with the privilege written as given.
    """
    # No conversion: the information_schema underscore form is emitted verbatim
    # (verified that UC's GRANT parser accepts USE_CATALOG etc.).
    assert statement.grant_statement(_g("ADD", "g", priv, "catalog", "c")) == (
        f"GRANT {priv} ON CATALOG `c` TO `g`")


# ---- tags: SET / UNSET / column split / CHANGE --------------------------

def test_tag_set_table():
    """An ADD tag on a table becomes ALTER TABLE … SET TAGS.

    Expected: exactly ALTER TABLE `asset_prod`.`gold`.`investment_accounts` SET TAGS
    ('acm_business_function' = 'investments').
    """
    assert statement.tag_statement(_t("ADD", "table", "asset_prod.gold.investment_accounts",
                                      "acm_business_function", "investments")) == (
        "ALTER TABLE `asset_prod`.`gold`.`investment_accounts` "
        "SET TAGS ('acm_business_function' = 'investments')")


def test_tag_change_is_set():
    """A CHANGE tag (a new value) is applied with SET TAGS, which overwrites the old value.

    Expected: the statement starts with ALTER TABLE `c`.`s`.`t` SET TAGS.
    """
    assert statement.tag_statement(_t("CHANGE", "table", "c.s.t", "acm_business_function",
                                      "compliance")).startswith("ALTER TABLE `c`.`s`.`t` SET TAGS")


def test_tag_unset_table():
    """A REMOVE tag becomes UNSET TAGS.

    Expected: exactly ALTER TABLE `c`.`s`.`t` UNSET TAGS ('acm_pii_type').
    """
    assert statement.tag_statement(_t("REMOVE", "table", "c.s.t", "acm_pii_type")) == (
        "ALTER TABLE `c`.`s`.`t` UNSET TAGS ('acm_pii_type')")


def test_tag_set_column_splits_4part_fqn():
    """A column tag splits the 4-part name into the table and an ALTER COLUMN clause.

    Expected: exactly ALTER TABLE `asset_prod`.`gold`.`investment_accounts` ALTER COLUMN
    `client_name` SET TAGS ('acm_pii_type' = 'FULL_NAME').
    """
    assert statement.tag_statement(_t("ADD", "column",
                                      "asset_prod.gold.investment_accounts.client_name",
                                      "acm_pii_type", "FULL_NAME")) == (
        "ALTER TABLE `asset_prod`.`gold`.`investment_accounts` "
        "ALTER COLUMN `client_name` SET TAGS ('acm_pii_type' = 'FULL_NAME')")


def test_tag_column_bad_fqn_raises():
    """A column tag whose name has only 3 parts (no column) is refused.

    Expected: ApplyError is raised.
    """
    with pytest.raises(statement.ApplyError):
        statement.tag_statement(_t("ADD", "column", "c.s.t", "acm_pii_type", "X"))  # only 3 parts


def test_tag_value_single_quote_escaped():
    """A single quote in a tag value is escaped in the SQL literal.

    Expected: SET TAGS ('acm_x' = 'O''Brien').
    """
    assert statement.tag_statement(_t("ADD", "table", "c.s.t", "acm_x", "O'Brien")) == (
        "ALTER TABLE `c`.`s`.`t` SET TAGS ('acm_x' = 'O''Brien')")


# ---- principal quoting --------------------------------------------------

def test_principal_quoting_group_and_email():
    """Group names and email principals are both backtick-quoted.

    Expected: the statement ends with TO `hera-adbk-x` for the group and TO `a@b.com` for the email.
    """
    assert statement.grant_statement(_g("ADD", "hera-adbk-x", "SELECT", "table", "c.s.t")).endswith(
        "TO `hera-adbk-x`")
    assert statement.grant_statement(_g("ADD", "a@b.com", "SELECT", "table", "c.s.t")).endswith(
        "TO `a@b.com`")


# ---- ABAC column-mask policies ------------------------------------------

def _pdef(scope="hera-dev.gold", name="acm_policy_mask_full_name__habc",
          fn="hera-dev.governance.acm_mask_full_name", col_val="FULL_NAME", exempt=""):
    return {"scope": scope, "policy_name": name, "function": fn,
            "column_tag_key": "acm_pii_type", "column_tag_value": col_val,
            "except_principals": exempt}


def _prow(change_type, scope="hera-dev.gold", name="acm_policy_mask_full_name__habc"):
    return {"scope": scope, "policy_name": name, "change_type": change_type}


def test_policy_create_or_replace_basic():
    """An ADD column-mask policy becomes a full CREATE OR REPLACE POLICY statement.

    Expected: ON SCHEMA `hera-dev`.`gold`, COLUMN MASK `hera-dev`.`governance`.`acm_mask_full_name`,
    TO `account users`, FOR TABLES, MATCH COLUMNS has_tag_value('acm_pii_type', 'FULL_NAME') AS c,
    ending ON COLUMN c; no EXCEPT and no WHEN.
    """
    sql = statement.policy_statement(_prow("ADD"), _pdef())
    assert sql.startswith("CREATE OR REPLACE POLICY acm_policy_mask_full_name__habc "
                          "ON SCHEMA `hera-dev`.`gold`")
    assert "COLUMN MASK `hera-dev`.`governance`.`acm_mask_full_name`" in sql
    assert "TO `account users`" in sql
    assert "FOR TABLES" in sql
    assert "MATCH COLUMNS has_tag_value('acm_pii_type', 'FULL_NAME') AS c" in sql
    assert sql.rstrip().endswith("ON COLUMN c")
    assert "EXCEPT" not in sql            # no exempt principals -> no EXCEPT
    assert "WHEN" not in sql              # no table narrowing: every table in scope


def test_policy_create_with_except():
    """A policy with exempt principals includes the EXCEPT clause, and never a WHEN (there is no
    table narrowing).

    Expected: EXCEPT `hera-etl-ingest`, `sp-pipelines`; no WHEN.
    """
    sql = statement.policy_statement(_prow("CHANGE"), _pdef(exempt="hera-etl-ingest,sp-pipelines"))
    assert "EXCEPT `hera-etl-ingest`, `sp-pipelines`" in sql
    assert "WHEN" not in sql


def test_policy_change_uses_create_or_replace():
    """A CHANGE policy is re-created rather than altered.

    Expected: the statement starts with CREATE OR REPLACE POLICY.
    """
    assert statement.policy_statement(_prow("CHANGE"), _pdef()).startswith("CREATE OR REPLACE POLICY")


def test_policy_remove_is_drop():
    """A REMOVE policy becomes DROP POLICY on its scope, with no definition needed.

    Expected: exactly DROP POLICY acm_policy_z ON TABLE `hera-dev`.`gold`.`t`.
    """
    sql = statement.policy_statement(_prow("REMOVE", scope="hera-dev.gold.t", name="acm_policy_z"), None)
    assert sql == "DROP POLICY acm_policy_z ON TABLE `hera-dev`.`gold`.`t`"


def test_policy_scope_keyword_by_depth():
    """The policy's ON keyword follows the depth of its scope name.

    Expected: ON CATALOG for cat, ON SCHEMA for cat.sch, ON TABLE for cat.sch.t.
    """
    assert " ON CATALOG " in statement.policy_statement(_prow("ADD", scope="cat"), _pdef(scope="cat"))
    assert " ON SCHEMA " in statement.policy_statement(_prow("ADD", scope="cat.sch"), _pdef(scope="cat.sch"))
    assert " ON TABLE " in statement.policy_statement(_prow("ADD", scope="cat.sch.t"), _pdef(scope="cat.sch.t"))


def test_policy_add_without_def_raises():
    """Adding a policy without its definition is refused.

    Expected: ApplyError is raised.
    """
    with pytest.raises(statement.ApplyError):
        statement.policy_statement(_prow("ADD"), None)


# ---- dynamic views (view-based RLS) -------------------------------------

def test_view_statement_pass_through():
    """A view with no rls_column is a plain SELECT * from its source.

    Expected: exactly CREATE OR REPLACE VIEW `hera-dev`.`dm_esg`.`dim_fund` AS SELECT * FROM `hera-
    dev`.`silver`.`dim_fund`.
    """
    sql = statement.view_statement(
        {"view": "hera-dev.dm_esg.dim_fund", "source": "hera-dev.silver.dim_fund"},
        entitlement_table="hera-dev.governance.group_portfolio_access")
    assert sql == ("CREATE OR REPLACE VIEW `hera-dev`.`dm_esg`.`dim_fund` AS "
                   "SELECT * FROM `hera-dev`.`silver`.`dim_fund`")


def test_view_statement_rls_semijoin():
    """A view with an rls_column filters rows by a semi-join against the entitlement table for the
    caller's groups.

    Expected: SELECT f.* FROM the source f WHERE f.`portfolio_code` IN (SELECT `portfolio_code` FROM
    `hera-dev`.`governance`.`group_portfolio_access` WHERE is_account_group_member(`ad_group`)).
    """
    sql = statement.view_statement(
        {"view": "hera-dev.dm_esg.fact_portfolio_position",
         "source": "hera-dev.silver.fact_portfolio_position", "rls_column": "portfolio_code"},
        entitlement_table="hera-dev.governance.group_portfolio_access")
    assert sql.startswith("CREATE OR REPLACE VIEW `hera-dev`.`dm_esg`.`fact_portfolio_position` AS")
    assert "SELECT f.* FROM `hera-dev`.`silver`.`fact_portfolio_position` f" in sql
    assert "WHERE f.`portfolio_code` IN (" in sql
    assert "SELECT `portfolio_code` FROM `hera-dev`.`governance`.`group_portfolio_access`" in sql
    assert "WHERE is_account_group_member(`ad_group`))" in sql


def test_view_statement_custom_group_col():
    """The RLS semi-join uses the configured group column name.

    Expected: is_account_group_member(`grp`) when group_col is grp.
    """
    sql = statement.view_statement({"view": "c.s.v", "source": "c.s.t", "rls_column": "pc"},
                                   entitlement_table="c.g.gpa", group_col="grp")
    assert "is_account_group_member(`grp`)" in sql


def test_drop_view_statement():
    """Dropping a view uses DROP VIEW IF EXISTS so it can be re-run safely.

    Expected: exactly DROP VIEW IF EXISTS `hera-dev`.`dm_esg`.`dim_fund`.
    """
    assert statement.drop_view_statement("hera-dev.dm_esg.dim_fund") == \
        "DROP VIEW IF EXISTS `hera-dev`.`dm_esg`.`dim_fund`"


# ---- dispatch / ordering ------------------------------------------------

def test_statements_for_policies_looks_up_defs():
    """Policy rows are turned into SQL using each policy's definition, looked up by (scope, name).

    Expected: the ADD row gives CREATE OR REPLACE POLICY and the REMOVE row gives DROP POLICY
    acm_policy_gone.
    """
    rows = [_prow("ADD"), _prow("REMOVE", name="acm_policy_gone")]
    defs = {("hera-dev.gold", "acm_policy_mask_full_name__habc"): _pdef()}
    out = statement.statements_for("policies", rows, policy_defs=defs)
    sqls = {r["change_type"]: s for r, s in out}
    assert sqls["ADD"].startswith("CREATE OR REPLACE POLICY")
    assert sqls["REMOVE"].startswith("DROP POLICY acm_policy_gone")


def test_statements_for_orders_add_before_remove():
    """Within a dimension, ADDs are emitted before REMOVEs, so new access is granted before old
    access is revoked.

    Expected: change-type order [ADD, REMOVE].
    """
    rows = [
        _g("REMOVE", "g", "SELECT", "table", "c.s.old"),
        _g("ADD", "g", "SELECT", "table", "c.s.new"),
    ]
    cts = [row["change_type"] for row, _ in statement.statements_for("grants", rows)]
    assert cts == ["ADD", "REMOVE"]


def _all_deltas():
    """One ADD and one REMOVE in every dimension (tags: a CHANGE too)."""
    return {
        "grants": [_g("REMOVE", "g", "SELECT", "table", "c.s.old"),
                   _g("ADD", "g", "SELECT", "table", "c.s.t")],
        "tags": [_t("REMOVE", "table", "c.s.old", "acm_x"), _t("CHANGE", "table", "c.s.u", "acm_x", "w"),
                 _t("ADD", "table", "c.s.t", "acm_x", "v")],
        "policies": [_prow("REMOVE", name="acm_policy_gone"), _prow("ADD")],
    }


_DEFS = {("hera-dev.gold", "acm_policy_mask_full_name__habc"): _pdef()}


def test_plan_all_tightens_first_and_loosens_last():
    """plan_all puts masking first (policies, then tags), then access (grants ADD before REMOVE),
    then loosening (UNSET tags, DROP policies), so nothing is readable unmasked mid-apply.

    Expected: (dimension, change_type) order policies ADD, tags CHANGE/ADD, grants ADD, grants
    REMOVE, tags REMOVE, policies REMOVE; every statement is a SQL string.
    """
    planned = statement.plan_all(_all_deltas(), policy_defs=_DEFS)
    assert [(d, r["change_type"]) for d, r, _ in planned] == [
        ("policies", "ADD"), ("tags", "ADD"), ("tags", "CHANGE"),
        ("grants", "ADD"), ("grants", "REMOVE"),
        ("tags", "REMOVE"), ("policies", "REMOVE")]
    assert all(isinstance(stmt, str) for _d, _r, stmt in planned)


def test_plan_phase_splits_the_delta_without_losing_rows():
    """Each phase takes only its own rows, and together the phases cover the whole delta once.

    Expected: masking has no REMOVE, access only grants, loosen only REMOVEs; 7 rows in total.
    """
    deltas = _all_deltas()
    by_phase = {ph: statement.plan_phase(ph, deltas, policy_defs=_DEFS) for ph in statement.PHASE_ORDER}
    assert all(r["change_type"] != "REMOVE" for _d, r, _ in by_phase["masking"])
    assert {d for d, _r, _ in by_phase["access"]} == {"grants"}
    assert all(r["change_type"] == "REMOVE" for _d, r, _ in by_phase["loosen"])
    assert sum(len(v) for v in by_phase.values()) == sum(len(v) for v in deltas.values())


def test_plan_phase_refuses_unknown_change_type_and_phase():
    """A row whose change_type no phase handles is refused, not silently dropped; so is an
    unknown phase name.

    Expected: ApplyError for a BOGUS tag row (in any phase) and for phase "widgets".
    """
    deltas = {"tags": [_t("BOGUS", "table", "c.s.t", "acm_x", "v")]}
    for phase in ("masking", "loosen"):
        with pytest.raises(statement.ApplyError, match="unexpected tags change_type"):
            statement.plan_phase(phase, deltas)
    with pytest.raises(statement.ApplyError, match="unknown phase"):
        statement.plan_phase("widgets", {})


def test_unknown_dimension_raises():
    """Asking for statements of an unknown dimension is refused.

    Expected: ApplyError is raised.
    """
    with pytest.raises(statement.ApplyError):
        statement.statements_for("widgets", [])


# ---- defensive branches: unexpected change types -----------------------

@pytest.mark.parametrize("build", [
    pytest.param(lambda: statement.grant_statement(_g("CHANGE", "g", "SELECT", "table", "c.s.t")),
                 id="grant_change"),          # grants have no CHANGE — only ADD / REMOVE
    pytest.param(lambda: statement.tag_statement(_t("BOGUS", "table", "c.s.t", "acm_x", "v")),
                 id="tag_bogus"),
    pytest.param(lambda: statement.policy_statement(_prow("BOGUS"), _pdef()),
                 id="policy_bogus"),
])
def test_unexpected_change_type_raises(build):
    """For each builder (grant, tag, policy), a change type it doesn't handle is refused rather than
    silently turned into SQL; grants in particular have no CHANGE.

    Expected: ApplyError mentioning "unexpected … change_type" for every case.
    """
    with pytest.raises(statement.ApplyError, match=r"unexpected \w+ change_type"):
        build()
