# Account groups from config/reference/<env>.yaml `groups` (a list of { name, external_id }).
#
# Each group's external_id picks how it is provisioned:
#   set   (AIM)    resolved from Microsoft Entra ID by its external_id (the Entra Object ID).
#                  Resolving PROVISIONS the group into the account if it isn't there yet — AIM makes
#                  Entra groups searchable but does not add them on its own.
#   null  (local)  created as a plain Databricks-managed account group, no Entra link.

# Account-level Databricks provider (groups are account objects, not workspace objects).
provider "databricks" {
  alias   = "account"
  profile = var.databricks_account_profile
}

locals {
  # group name -> external_id (Entra Object ID, or null for a local group).
  groups        = { for g in try(local.reference.groups, []) : g.name => try(g.external_id, null) }
  linked_groups = { for name, ext in local.groups : name => ext if ext != null }
  local_groups  = toset([for name, ext in local.groups : name if ext == null])
}

# The account id (needed for the external-group resource name).
data "databricks_current_config" "account" {
  provider = databricks.account
}

# 1 Groups WITH an external_id: resolve + provision by Entra Object ID (IAM v2 external-group API,
#   Beta). NOTE this is a DATA source with a side effect — reading it provisions the group, so it
#   happens at `plan`, and `terraform destroy` never removes it. The postcondition fails closed if
#   the resolved Entra group's name differs from the reference name (catches a mistyped GUID).
data "databricks_account_iam_external_group_v2" "this" {
  provider = databricks.account
  for_each = local.linked_groups
  name     = "accounts/${data.databricks_current_config.account.account_id}/external-groups/${each.value}"

  lifecycle {
    postcondition {
      condition     = self.display_name == each.key
      error_message = "external_id for '${each.key}' resolves to Entra group '${self.display_name}' — the GUID or the name in config/reference/${var.env}.yaml is wrong."
    }
  }
}

# 2 Groups WITHOUT an external_id: plain Databricks-managed account groups.
resource "databricks_group" "this" {
  provider     = databricks.account
  for_each     = local.local_groups
  display_name = each.value
  force        = true # adopt a same-named group that already exists instead of erroring
}
