data "azurerm_storage_account" "storage" {
  resource_group_name = var.resource_group_name

  name = var.storage_account_name
}