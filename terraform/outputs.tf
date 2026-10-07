output "catalog" {
  description = "The pre-existing target catalog these schemas are created under (referenced by name, not created)."
  value       = local.catalog
}

output "schemas" {
  description = "Schemas provisioned under the catalog (governance + serving)."
  value       = sort([for s in databricks_schema.this : s.name])
}

output "groups" {
  description = "Account groups by name -> Databricks group id (AIM-resolved or locally created)."
  value = merge(
    { for name, g in data.databricks_account_iam_external_group_v2.this : name => g.internal_id },
    { for name, g in databricks_group.this : name => g.id },
  )
}
