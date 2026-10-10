# Azure landing layer (Terraform)

Creates the Azure File Share used as the **landing layer** of the grid report ETL pipeline:

```
SFTP (homelab) ──jobs/land_*_sftp_to_azure.py──▶ Azure File Share "landing"/Grid_Report/{YYYY}/{MM}/ ──▶ Spark
```

| Resource | Settings |
|---|---|
| Storage account `homelablandfileshare` | StorageV2, Standard, LRS, UK South, HTTPS only, TLS 1.2, in the existing `rg-datalake-test-uksouth` |
| File share `landing` | Pay-as-you-go, Transaction optimized tier, 5 GB quota, SMB |

## Usage

```powershell
az login
cd infra\azure-landing
copy terraform.tfvars.example terraform.tfvars   # set subscription_id
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

Copy the connection string into the project's `.env` (`AZURE_STORAGE_CONNECTION_STRING=`):

```powershell
terraform output -raw primary_connection_string
```

## Notes

- **State contains secrets.** `terraform.tfstate` holds the storage account keys. It stays local and is gitignored, as are `*.tfvars`. Commit `.terraform.lock.hcl`.
- **The resource group isn't managed here.** It's read with a `data` source, so `terraform destroy` removes only the storage account and share.
- **azurerm 5.x doesn't register resource providers automatically.** `Microsoft.Storage` was already registered on the subscription.
- **Rotating the key:** rotate it in the portal (*Access keys → Rotate key*), then run `terraform refresh` and copy the new connection string into `.env`.
