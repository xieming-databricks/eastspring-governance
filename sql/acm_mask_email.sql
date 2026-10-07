-- Column-mask UDF for acm_pii_type = EMAIL.
-- Referenced by the ABAC policy: COLUMN MASK `hera-dev`.governance.acm_mask_email
-- TODO(governance): confirm the masking transform and the unmasked-group list.
CREATE OR REPLACE FUNCTION `hera-dev`.governance.acm_mask_email(v STRING)
RETURNS STRING
RETURN CASE
         WHEN is_account_group_member('hera-all-esg-read-unmasked')
           OR is_account_group_member('hera-all-admin-admin-unmasked')
         THEN v
         ELSE regexp_replace(v, '^[^@]+', '***')   -- e.g. "jane@x.com" -> "***@x.com"
       END;
