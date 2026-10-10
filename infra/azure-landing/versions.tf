terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.8"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  # Uses your `az login` session. Microsoft.Storage is already registered on the
  # subscription; azurerm 5.x no longer registers resource providers by default.
}
