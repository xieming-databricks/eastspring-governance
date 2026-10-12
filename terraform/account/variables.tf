variable "env" {
  description = "Deployment env — selects config/reference/<env>.yaml (e.g. esg)."
  type        = string
  default     = "esg"
}

variable "databricks_account_profile" {
  description = "Account-level Databricks CLI profile (host=accounts.azuredatabricks.net + account_id) for account groups."
  type        = string
  default     = "azure-account-ccb842e7"
}
