terraform {
  required_version = ">= 1.5"
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = ">= 1.50"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 2.50"
    }
  }
}
