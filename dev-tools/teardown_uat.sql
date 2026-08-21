-- Remove the config/uat e2e fixtures. CASCADE drops schemas/tables/views/functions/
-- policies/tags/grants under the catalog in one shot.
DROP CATALOG IF EXISTS asset_prod CASCADE;
