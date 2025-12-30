# Deployment Guide: Ski Analysis App on Azure

## 1. Prerequisites
- Azure Subscription
- Azure CLI installed (`az login`)
- VS Code with "Azure App Service" extension

## Fast Deployment (Recommended)
We have created a script `deploy.ps1` that automates zipping, setting environment variables from your `.env` file, and deploying.

1.  **Login to Azure**:
    ```powershell
    az login
    ```

2.  **Run the Deployment Script**:
    ```powershell
    .\deploy.ps1
    ```

**Target Details:**
- **App Name**: `skiaicoach`
- **Resource Group**: `rg-shelfeng-test-ai`
- **URL**: `https://skiaicoach.azurewebsites.net`

## Manual Steps (Reference)
### Configure Environment Variables
```powershell
az webapp config appsettings set --resource-group rg-shelfeng-test-ai --name skiaicoach --settings @.env
```

### Deploy Code
```powershell
az webapp deployment source config-zip --resource-group rg-shelfeng-test-ai --name skiaicoach --src app.zip
```

## 4. Configure Managed Identity (Recommended)
1.  **Enable Identity**:
    *   App Service -> **Identity** -> Status: **On** -> Save.
2.  **Grant Access**:
    *   Storage Account -> **Access Control (IAM)** -> **Add role assignment**.
    *   Role: **Storage Blob Data Contributor**.
    *   Member: Select your App Service (Managed Identity).


**Option B: ZIP Deploy**
```bash
# Zip your project (exclude .env, venv, __pycache__)
az webapp deployment source config-zip --resource-group SkiAppGroup --name <your-app-name> --src app.zip
```

## 6. Startup Command
Go to **Configuration** -> **General Settings**:
- Startup Command: `gunicorn --bind=0.0.0.0 --timeout 600 app:app`

