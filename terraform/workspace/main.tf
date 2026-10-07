provider "databricks" {
  profile = var.databricks_profile
}

# Read the deployment's reference allow-list — the single source of truth for the catalog + schemas
# this env is authoritative over. Terraform owns these *containers*; the governance framework fills
# them (grants/tags/policies/views/UDFs) and the customer supplies entitlement/base data.
locals {
  reference = yamldecode(file("${path.module}/../../config/reference/${var.env}.yaml"))
  catalog   = local.reference.target_catalog

  # Terraform creates only the schemas it is authoritative over: governance (mask UDFs, gpa MV,
  # entitlement tables) + serving (dynamic-view destinations). source_schemas (gold/silver) hold
  # DE-owned base tables the framework only READS from, so they are NOT provisioned here. Entries
  # are fully-qualified "<catalog>.<schema>"; keep those under target_catalog, strip the prefix.
  all_schemas = concat(
    try(local.reference.governance_schemas, []),
    try(local.reference.serving_schemas, []),
  )
  schemas = toset([
    for s in local.all_schemas :
    trimprefix(s, "${local.catalog}.")
    if startswith(s, "${local.catalog}.")
  ])
}

# The target catalog is a customer / metastore-admin-owned container, pre-created outside this
# module — we do NOT create or look it up, we just create schemas under it by name. If the catalog
# is missing, the schema create fails at apply (fail-closed); nothing here creates the catalog.
resource "databricks_schema" "this" {
  for_each      = local.schemas
  catalog_name  = local.catalog
  name          = each.value
  force_destroy = var.force_destroy
  comment       = "Provisioned by Terraform from config/reference/${var.env}.yaml"
}
