-- TEST FIXTURE — a self-contained copy for the unit tests. The real templates are deployment
-- content and live in the configs repo (sql/<env>/); keep this copy in step when the format changes.
-- Column-mask UDF for acm_pii_type = PASSPORT.
-- Referenced by the ABAC policy: COLUMN MASK `${catalog}`.`${governance_schema}`.acm_mask_passport
-- The WHEN condition is GENERATED from the reference's `groups` (every group named *unmasked
-- reads the raw value; none -> FALSE, everyone masked). TODO(governance): confirm the transform.
CREATE OR REPLACE FUNCTION `${catalog}`.`${governance_schema}`.acm_mask_passport(v STRING)
RETURNS STRING
RETURN CASE
         WHEN ${unmasked_condition}
         THEN v
         ELSE '********'                          -- full redaction
       END;
