-- Column-mask UDF for acm_pii_type = FULL_NAME.
-- Referenced by the ABAC policy: COLUMN MASK `hera-dev`.governance.acm_mask_full_name
-- Returns the raw value for members of a business "read-unmasked" group; masks everyone else.
-- ETL/pipeline identities are exempted at the POLICY level (EXCEPT), not here.
-- TODO(governance): confirm the masking transform and the unmasked-group list.
CREATE OR REPLACE FUNCTION `hera-dev`.governance.acm_mask_full_name(v STRING)
RETURNS STRING
RETURN CASE
         WHEN is_account_group_member('hera-all-esg-read-unmasked')
           OR is_account_group_member('hera-all-admin-admin-unmasked')
         THEN v
         ELSE concat(substr(v, 1, 1), '***')   -- e.g. "Jane Doe" -> "J***"
       END;
