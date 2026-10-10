variable "subscription_id" {
  description = "Azure subscription to deploy into."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group that holds the storage account."
  type        = string
  default     = "rg-datalake-test-uksouth"
}

variable "storage_account_name" {
  description = "Globally unique, 3-24 lowercase letters/digits."
  type        = string
  default     = "homelablandfileshare"

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "Storage account names must be 3-24 lowercase letters or digits."
  }
}

variable "file_share_name" {
  description = "The landing file share (matches AZURE_FILE_SHARE_NAME in the project's .env)."
  type        = string
  default     = "landing"
}

variable "file_share_quota_gb" {
  description = "Maximum share size in GB. Pay-as-you-go bills for used capacity, not the quota."
  type        = number
  default     = 5
}

variable "tags" {
  type = map(string)
  default = {
    project     = "spark-homelab"
    layer       = "landing"
    environment = "test"
    managed_by  = "terraform"
  }
}
