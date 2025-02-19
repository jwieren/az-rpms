output "func_name" {
  value = azurerm_linux_function_app.function.name
}

output "func_id" {
  value = azurerm_linux_function_app.function.id
}

output "func_base_url" {
  value = local.func_base_url
}

output "verify_url" {
  value = "${local.func_base_url}/api/verify"
}

output "managed_identity_principal_id" {
  value = one(azurerm_linux_function_app.function.identity[*].principal_id)
}
