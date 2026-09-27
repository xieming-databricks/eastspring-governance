# `sql/` — git-managed UDF definitions (source of truth for function bodies)

Each file holds **exactly one** `CREATE OR REPLACE FUNCTION` statement (idempotent). These are the
mask UDFs the ABAC column-mask policies reference (`COLUMN MASK <catalog>.governance.acm_mask_*`).

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
