# Installing resource power mgmt

## Pre-requisites

There are a few pre-requisites needed to install this module.

1. A resource group 
2. A user assigned identity.
3. A log analytics workspace, used for app insights instance
4. Create a storage account, of type `account_kind:StorageV2`
5. A custom role with the following permissions:

```
    actions = [
      # Virtual machines
      "Microsoft.Compute/virtualMachines/start/action",
      "Microsoft.Compute/virtualMachines/restart/action",
      "Microsoft.Compute/virtualMachines/deallocate/action",
      "Microsoft.Compute/virtualMachines/write",                 # required for tags
      "Microsoft.Network/networkInterfaces/join/action",         # required for assigning ips to nics
      "Microsoft.Compute/disks/write",                           # required for disks
      # AKS / container services
      "Microsoft.ContainerService/managedClusters/start/action",
      "Microsoft.ContainerService/managedClusters/stop/action",
      "Microsoft.ContainerService/write",                        # required for tags
      # App Gateways
      "Microsoft.Network/applicationGateways/start/action",
      "Microsoft.Network/applicationGateways/stop/action",
      "Microsoft.Network/applicationGateways/restart/action",
      "Microsoft.Network/applicationGateways/write",             # required for tags
      "Microsoft.Network/virtualNetworks/subnets/join/action",   # required for vnet integration
      # PostgreSQL Flexible
      "Microsoft.DBforPostgreSQL/flexibleServers/start/action",
      "Microsoft.DBforPostgreSQL/flexibleServers/restart/action",
      "Microsoft.DBforPostgreSQL/flexibleServers/stop/action",
      "Microsoft.DBforPostgreSQL/flexibleServers/write",         # required for tags
      # Metrics
      "Microsoft.Insights/MetricAlerts/Read",
      "Microsoft.Insights/MetricAlerts/Write",
      # Storage account
      "Microsoft.Storage/storageAccounts/write"                  # required to enable/disable sftp service
    ]
```

6. The following role assignments to the **user assigned identity**:
  - At the scope of management group(s) you wish to manage resources over:
    - The custom role defined above
    - `Managed Identity Operator`    # Required to stop/start resources that have linked identities
    - `Monitoring Contributor`       # Required to suppress azure monitor alerts for the affected resources
    - `Tag Contributor`              # To update tags on resources, where it is supported

  - At the parent scope of the management group(s) you wish to manage resources over:
    - `Reader`

  - At the scope of the storage account defined above:
    - `Storage Queue Data Contributor`

## Usage

There are a number of parameters to specify, but most are self-explanatory. A simple setup might look like this:

```terraform
module "resource_power_mgmt" {
  source  = "..."
  version = "1.0.1"

  deploy_from_working_dir          = "${path.cwd}/functions"
  resource_group_name              = "rg_powermgmt"
  location                         = "australiaeast"
  func_resource_name               = "func_powermgmt_prd"
  storage_account_name             = "stpowermgmt"
  app_insights_name                = "ai_powermgmt_prd"
  app_insights_workspace_id        = "..."
  resourcegraph_mg_scope           = "tenant_root_group"
  user_assigned_identity_id        = "..."
  user_assigned_identity_client_id = "..."
  mg_scopes                        = ["mg_workloads", "mg_platform"]
  queue_updatemgmt_events          = "queue_sub_updatemgmt_events"
  workbook_more_info_url           = "..."
  tags                             = {}

  az_cli_login_args = "--service-principal -u $ARM_CLIENT_ID -p $ARM_CLIENT_SECRET --tenant $ARM_TENANT_ID"
}

```