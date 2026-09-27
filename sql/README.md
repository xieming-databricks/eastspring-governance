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

**Deploy:** the `notebooks/apply_functions.py` notebook applies every `sql/*.sql` here. CI/CD runs
that notebook **only when files under `sql/**` change** (path-filtered trigger), so the live UDFs
track git. `CREATE OR REPLACE` makes re-runs no-ops.

**Convention**
- One statement per file (the applier runs each file as a single `spark.sql` call — no `;`-splitting).
- Names fully qualified and backtick-quoted (`` `hera-dev` `` contains a hyphen).
- The `governance` schema must already exist (seeded/Terraform).

**Masking model (confirm with governance):** the policy applies `TO account users EXCEPT <ETL>` —
ETL/pipeline identities are exempted at the *policy* level (`config/governance/esg.yaml`). The
*business* "read-unmasked" groups are exempted here, inside the function, via
`is_account_group_member(...)`. The transforms below are **placeholders** — replace with the
approved masking logic.
