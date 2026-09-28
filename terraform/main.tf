provider "databricks" {
  profile = var.databricks_profile
}

# Read the deployment's reference allow-list — the single source of truth for the catalog + schemas
# this env is authoritative over. Terraform owns these *containers*; the governance framework fills
# them (grants/tags/policies/views/UDFs) and the customer supplies entitlement/base data.
locals {
  reference = yamldecode(file("${path.module}/../config/reference/${var.env}.yaml"))
  catalog   = local.reference.target_catalog

  # `schemas` entries are fully-qualified "<catalog>.<schema>"; keep the ones under target_catalog
  # and strip the catalog prefix to the bare schema name.
  schemas = toset([
    for s in try(local.reference.schemas, []) :
    trimprefix(s, "${local.catalog}.")
    if startswith(s, "${local.catalog}.")
  ])
}

resource "databricks_catalog" "this" {
  name          = local.catalog
  storage_root  = var.storage_root
  force_destroy = var.force_destroy
  comment       = "Provisioned by Terraform from config/reference/${var.env}.yaml"
}

resource "databricks_schema" "this" {
  for_each     = local.schemas
  catalog_name = databricks_catalog.this.name
  name         = each.value
  comment      = "Provisioned by Terraform from config/reference/${var.env}.yaml"
}
