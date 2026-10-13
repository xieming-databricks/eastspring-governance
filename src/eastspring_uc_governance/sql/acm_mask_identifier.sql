-- Column-mask UDF for acm_pii_type = identifier (e.g. portfolio_code).
-- Referenced by the ABAC policy: COLUMN MASK `${catalog}`.`${governance_schema}`.acm_mask_identifier
-- TODO(governance): confirm the masking transform and the unmasked-group list. A deterministic
-- hash keeps the value join-able/consistent while hiding it; swap for redaction if not wanted.
CREATE OR REPLACE FUNCTION `${catalog}`.`${governance_schema}`.acm_mask_identifier(v STRING)
RETURNS STRING
RETURN CASE
         WHEN is_account_group_member('hera-all-esg-read-unmasked')
           OR is_account_group_member('hera-all-admin-admin-unmasked')
         THEN v
         ELSE sha2(v, 256)                        -- deterministic pseudonym
       END;
