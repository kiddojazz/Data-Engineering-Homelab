output "storage_account_name" {
  value = azurerm_storage_account.landing.name
}

output "file_share_name" {
  value = azurerm_storage_share.landing.name
}

output "file_share_url" {
  value = azurerm_storage_share.landing.url
}

# Value for AZURE_STORAGE_CONNECTION_STRING in the project's .env:
#   terraform output -raw primary_connection_string
output "primary_connection_string" {
  value     = azurerm_storage_account.landing.primary_connection_string
  sensitive = true
}
