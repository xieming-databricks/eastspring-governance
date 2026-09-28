variable "env" {
  description = "Deployment env — selects config/reference/<env>.yaml (e.g. esg)."
  type        = string
  default     = "esg"
}

variable "databricks_profile" {
  description = "Workspace-level Databricks CLI profile (~/.databrickscfg) for catalog/schema resources."
  type        = string
  default     = "xieming-azure"
}

variable "databricks_account_profile" {
  description = "Account-level Databricks CLI profile (host=accounts.azuredatabricks.net + account_id) for creating account groups."
  type        = string
  default     = "azure-account-ccb842e7"
}

variable "entra_linked_groups" {
  description = "true: link each account group to its Entra ID group via external_id (AIM; needs Entra + az login). false: create plain local Databricks account groups (dev; no Entra / az login)."
  type        = bool
  default     = true
}

variable "storage_root" {
  description = "Optional managed storage location (abfss://…) for the catalog; null uses the metastore's default managed storage."
  type        = string
  default     = null
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to drop the catalog even if it still contains schemas/objects."
  type        = bool
  default     = false
}
