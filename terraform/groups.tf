# Account groups from config/reference/<env>.yaml `groups`.
#
# Automatic Identity Management (AIM) is enabled, so a Databricks account group must be linked to its
# Microsoft Entra ID group via `external_id` = the Entra group's object id (that's how membership
# syncs from Entra). So we first SEARCH Entra ID by display name to resolve the object id, then
# create the account group with that external_id. A group missing (or ambiguous) in Entra fails the
# plan — you can't create a linked group without its Entra counterpart.

provider "azuread" {
  # Authenticates via Azure CLI (`az login`) by default; override with ARM_TENANT_ID / a service
  # principal via the usual AzureAD provider env vars if running in CI.
}

# Account-level Databricks provider (groups are account objects, not workspace objects).
provider "databricks" {
  alias   = "account"
  profile = var.databricks_account_profile
}

locals {
  groups = toset(try(local.reference.groups, []))
}

# 1 Entra-linked mode only: resolve each group's Entra ID object id by display name (security
#    groups). Fails if not found or ambiguous — the intended fail-closed behavior. Skipped entirely
#    (for_each = []) when entra_linked_groups = false, so local mode needs no Entra / az login.
data "azuread_group" "entra" {
  for_each         = var.entra_linked_groups ? local.groups : toset([])
  display_name     = each.value
  security_enabled = true
}

# 2 Create the Databricks account group. Entra-linked -> external_id = the Entra object id (AIM
#    syncs membership); local -> external_id = null (plain Databricks-managed account group).
resource "databricks_group" "this" {
  provider     = databricks.account
  for_each     = local.groups
  display_name = each.value
  external_id  = var.entra_linked_groups ? data.azuread_group.entra[each.value].object_id : null
  force        = true # adopt a same-named group already provisioned by AIM/SCIM instead of erroring
}
