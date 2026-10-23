-- Column-mask UDF for acm_pii_type = identifier (e.g. portfolio_code).
-- Referenced by the ABAC policy: COLUMN MASK `${catalog}`.`${governance_schema}`.acm_mask_identifier
-- The WHEN condition is GENERATED from the reference's `groups` (every group named *unmasked
-- reads the raw value; none -> FALSE, everyone masked). TODO(governance): confirm the transform. A deterministic
-- hash keeps the value join-able/consistent while hiding it; swap for redaction if not wanted.
CREATE OR REPLACE FUNCTION `${catalog}`.`${governance_schema}`.acm_mask_identifier(v STRING)
RETURNS STRING
RETURN CASE
         WHEN ${unmasked_condition}
         THEN v
         ELSE sha2(v, 256)                        -- deterministic pseudonym
       END;
