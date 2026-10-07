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

variable "force_destroy" {
  description = "Allow `terraform destroy` to drop a schema even if it still contains objects (e.g. framework-created views in the serving schemas)."
  type        = bool
  default     = false
}
