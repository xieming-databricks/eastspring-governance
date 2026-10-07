-- Column-mask UDF for acm_pii_type = PASSPORT.
-- Referenced by the ABAC policy: COLUMN MASK `hera-dev`.governance.acm_mask_passport
-- TODO(governance): confirm the masking transform and the unmasked-group list.
CREATE OR REPLACE FUNCTION `hera-dev`.governance.acm_mask_passport(v STRING)
RETURNS STRING
RETURN CASE
         WHEN is_account_group_member('hera-all-esg-read-unmasked')
           OR is_account_group_member('hera-all-admin-admin-unmasked')
         THEN v
         ELSE '********'                          -- full redaction
       END;
