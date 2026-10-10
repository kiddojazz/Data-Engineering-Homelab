# Landing layer for the grid report ETL pipeline:
#   SFTP (homelab) -> Azure File Share "landing" -> Spark
# Files are written over HTTPS with the Azure Storage SDK (jobs/azure_landing_utils.py).

data "azurerm_resource_group" "datalake" {
  name = var.resource_group_name
}

resource "azurerm_storage_account" "landing" {
  name                = var.storage_account_name
  resource_group_name = data.azurerm_resource_group.datalake.name
  location            = data.azurerm_resource_group.datalake.location

  # Standard general-purpose v2 + LRS = pay-as-you-go file shares, cheapest redundancy
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true # the pipeline authenticates with the account connection string

  tags = var.tags
}

resource "azurerm_storage_share" "landing" {
  name               = var.file_share_name
  storage_account_id = azurerm_storage_account.landing.id
  quota              = var.file_share_quota_gb
  access_tier        = "TransactionOptimized"
  enabled_protocol   = "SMB"

  metadata = {
    purpose = "grid-report-landing"
  }
}
