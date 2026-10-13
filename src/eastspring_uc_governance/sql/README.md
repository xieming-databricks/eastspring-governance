# `sql/` — git-managed SQL objects (source of truth for function/derived-table definitions)

Each file holds **exactly one** idempotent `CREATE OR REPLACE …` statement. Today:
- `acm_mask_*.sql` — the mask UDFs the ABAC column-mask policies reference
  (`COLUMN MASK <catalog>.governance.acm_mask_*`).
- `group_portfolio_access.sql` — the RLS allow-list as a **materialized view** (`CREATE OR REPLACE
  MATERIALIZED VIEW … TRIGGER ON UPDATE`), derived from the customer-owned `entitlements` /
  `entitlement_mapping` tables; the dynamic views semi-join against it. `TRIGGER ON UPDATE` means it
  auto-refreshes when those source tables change. **Requires serverless compute** (Pro/Serverless
  SQL warehouse, or serverless general compute on DBR 18.1+) to create/refresh — so run these `sql/`
  objects on a SQL warehouse or serverless, not a classic cluster. (The two source tables are
  customer-owned; seed them for dev with `dev-tools/seed_esg_rls.sql`.)

**Packaged + templated:** these files ship **inside the `eastspring-uc-governance` wheel** and carry
no deployment specifics. The catalog and governance schema are placeholders, filled in per env by
`eastspring_uc_governance.sql_objects.render_sql_objects(reference)` from `config/reference/<env>.yaml`:

| Placeholder | From the reference |
|---|---|
| `${catalog}` | `target_catalog` |
| `${governance_schema}` | the one `governance_schemas` entry under `target_catalog` (bare name) |

Rendering fails loudly on a missing value or an unknown placeholder. A literal `$` must be written `$$`.

**Deploy:** the `notebooks/apply_functions.py` notebook installs the wheel (`%pip` from a UC Volume),
renders every `sql/*.sql` for the env, and applies it. `CREATE OR REPLACE` makes re-runs no-ops.

**Convention**
- One statement per file (the applier runs each file as a single `spark.sql` call — no `;`-splitting).
- Names fully qualified and backtick-quoted: `` `${catalog}`.`${governance_schema}`.<object> ``
  (catalog names like `hera-dev` contain a hyphen).
- The governance schema must already exist (`ucgov-provision schemas`, seed, or Terraform).
- **Still env-specific:** the mask UDFs name the unmasked groups (`hera-all-esg-read-unmasked`,
  `hera-all-admin-admin-unmasked`) literally — not yet a placeholder.

**Masking model (confirm with governance):** the policy applies `TO account users EXCEPT <ETL>` —
ETL/pipeline identities are exempted at the *policy* level (`mask_exempt_principals` in
`config/reference/esg.yaml`). The
*business* "read-unmasked" groups are exempted here, inside the function, via
`is_account_group_member(...)`. The transforms below are **placeholders** — replace with the
approved masking logic.
