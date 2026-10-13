-- Column-mask UDF for acm_pii_type = FULL_NAME.
-- Referenced by the ABAC policy: COLUMN MASK `${catalog}`.`${governance_schema}`.acm_mask_full_name
-- Returns the raw value for members of a business "read-unmasked" group; masks everyone else.
-- ETL/pipeline identities are exempted at the POLICY level (EXCEPT), not here.
-- The WHEN condition is GENERATED from the reference's `groups` (every group named *unmasked
-- reads the raw value; none -> FALSE, everyone masked). TODO(governance): confirm the transform.
CREATE OR REPLACE FUNCTION `${catalog}`.`${governance_schema}`.acm_mask_full_name(v STRING)
RETURNS STRING
RETURN CASE
         WHEN ${unmasked_condition}
         THEN v
         ELSE concat(substr(v, 1, 1), '***')   -- e.g. "Jane Doe" -> "J***"
       END;
