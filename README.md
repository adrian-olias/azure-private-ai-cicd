# Azure Private AI: app and CI/CD

A chat assistant that answers from your own documents. It runs on an Azure VM with no public IP.
GitHub Actions builds the images on pull requests and deploys them on merge.

Infrastructure: [terraform-azure-private-ai](https://github.com/adrian-olias/terraform-azure-private-ai)

## First deploy to Dev

1. Apply the infrastructure first, following its README.

2. Add these repository variables under Settings → Secrets and variables → Actions → Variables:

   - `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`: from the `github_oidc` output
   - `ACR_NAME`: from `container_registry_names`

3. Create an environment named `Dev` with these variables:

   - `RESOURCE_GROUP`: `rg-privateai-lab`
   - `VM_NAME`: `vm-privateai-lab`
   - `KEY_VAULT_NAME`: `kv-privateai-lab-<suffix>`
   - `AZURE_STORAGE_ACCOUNT`: `stprivateailab<suffix>`
   - `WEBUI_ADMIN_EMAIL`: the admin login for the chat

   The Key Vault and storage names are printed at the end of `terraform apply`.

4. In the Azure portal, open the Key Vault and create two secrets:

   - `webui-secret-key`: a long random string
   - `webui-admin-password`: the admin password

5. Upload your documents to the `documents` container of the storage account. They reach the assistant within 15 minutes.

6. Create the `dev` branch from `main`:

   ```bash
   git push origin main:dev
   ```

7. Open a pull request from `feature/<name>` to `dev` and merge it. The deploy runs on merge, and the model's answer appears in the run summary.

## Production

Create a `Production` environment with the same variables, pointing at its own VM. Add a required reviewer, so a merge from `dev` to `main` waits for approval.

## Layout

```
.github/workflows/build_and_push.yml   build on pull request, deploy on merge
services/docs-sync/                    syncs the Storage container into the knowledge base
services/webui-bootstrap/              creates the admin, knowledge base and custom model
docker-compose.yml                     the stack
```
