-- Column-mask UDF for acm_pii_type = EMAIL.
-- Referenced by the ABAC policy: COLUMN MASK `${catalog}`.`${governance_schema}`.acm_mask_email
-- The WHEN condition is GENERATED from the reference's `groups` (every group named *unmasked
-- reads the raw value; none -> FALSE, everyone masked). TODO(governance): confirm the transform.
CREATE OR REPLACE FUNCTION `${catalog}`.`${governance_schema}`.acm_mask_email(v STRING)
RETURNS STRING
RETURN CASE
         WHEN ${unmasked_condition}
         THEN v
         ELSE regexp_replace(v, '^[^@]+', '***')   -- e.g. "jane@x.com" -> "***@x.com"
       END;
