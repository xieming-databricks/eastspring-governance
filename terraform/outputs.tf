output "catalog" {
  description = "The pre-existing target catalog these schemas are created under (referenced by name, not created)."
  value       = local.catalog
}

output "schemas" {
  description = "Schemas provisioned under the catalog (governance + serving)."
  value       = sort([for s in databricks_schema.this : s.name])
}

output "groups" {
  description = "Account groups created, mapped to their resolved Entra ID external_id."
  value       = { for name, g in databricks_group.this : name => g.external_id }
}
