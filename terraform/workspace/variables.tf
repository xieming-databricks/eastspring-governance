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

variable "force_destroy" {
  description = "Allow `terraform destroy` to drop a schema even if it still contains objects (e.g. framework-created views in the serving schemas)."
  type        = bool
  default     = false
}
