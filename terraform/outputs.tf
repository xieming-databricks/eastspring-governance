output "catalog" {
  description = "The catalog provisioned for this env."
  value       = databricks_catalog.this.name
}

output "schemas" {
  description = "Schemas provisioned under the catalog (from reference.schemas)."
  value       = sort([for s in databricks_schema.this : s.name])
}

output "groups" {
  description = "Account groups created, mapped to their resolved Entra ID external_id."
  value       = { for name, g in databricks_group.this : name => g.external_id }
}
