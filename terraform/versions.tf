terraform {
  required_version = ">= 1.5"
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.131" # databricks_account_iam_external_group_v2 (AIM group resolve)
    }
  }
}
