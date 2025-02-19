resource "random_id" "id" {
  keepers     = {}
  byte_length = 8
}

locals {
  func_name                       = "app-${var.func_name}-${random_id.id.id}"
  queue_powermgmt_events          = "queue-powermgmt-events"
  queue_updatemgmt_events         = var.queue_updatemgmt_events
  queue_sub_processing            = "queue-sub-processing"
  queue_powermgmt_deferred_checks = "queue-powermgmt-deferred-checks"

  queues = [
    local.queue_powermgmt_events,
    local.queue_updatemgmt_events,
    local.queue_sub_processing,
    local.queue_powermgmt_deferred_checks
  ]

  app_settings = {
    AzureWebJobsFeatureFlags        = "EnableWorkerIndexing" # Required for Python func app v2
    STORAGE_ACCOUNT                 = var.storage_account_name
    QUEUE_POWERMGMT_EVENTS          = local.queue_powermgmt_events
    QUEUE_PROCESS_UPDATEMGMT_EVENT  = local.queue_updatemgmt_events
    QUEUE_PROCESS_SUBS              = local.queue_sub_processing
    QUEUE_POWERMGMT_DEFERRED_CHECKS = local.queue_powermgmt_deferred_checks
    MG_SCOPES                       = var.mg_scopes != null ? join(",", var.mg_scopes) : null
    SUB_SCOPES                      = var.sub_scopes != null ? join(",", var.sub_scopes) : null
    RESOURCEGRAPH_MG_SCOPE          = var.resourcegraph_mg_scope
    TRIGGER_INTERVAL                = 5
    FUNCTIONS_WORKER_PROCESS_COUNT  = 3
    CLIENT_ID                       = var.user_assigned_identity_client_id
  }
}

resource "azurerm_service_plan" "service_plan" {
  count = var.create_service_plan ? 1 : 0

  name                = var.service_plan_name
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Linux"
  sku_name            = "Y1"
  tags                = var.tags
}

resource "azurerm_application_insights" "logging" {
  count = var.create_app_insights ? 1 : 0

  name                = var.app_insights_name
  location            = var.location
  resource_group_name = var.resource_group_name
  application_type    = "web"
  tags                = var.tags
  workspace_id        = var.app_insights_workspace_id
}

resource "random_uuid" "insights_workbook" {}

resource "azurerm_application_insights_workbook" "workbook" {
  count = var.create_app_insights ? 1 : 0

  name                = random_uuid.insights_workbook.result
  location            = var.location
  resource_group_name = var.resource_group_name
  display_name        = "Power Management Workbook"
  tags                = var.tags

  data_json = templatefile("${path.module}/files/workbook.tftmpl", {
    linked_resource_id = azurerm_application_insights.logging[count.index].id
    storage_account_id = data.azurerm_storage_account.storage.id
    func_id            = azurerm_linux_function_app.function.id
    more_info_url      = var.workbook_more_info_url
  })
}

resource "azurerm_storage_queue" "queues" {
  for_each             = toset(local.queues)
  name                 = each.key
  storage_account_name = data.azurerm_storage_account.storage.name
}

resource "azurerm_linux_function_app" "function" {
  name                            = coalesce(var.func_resource_name, local.func_name)
  location                        = var.location
  resource_group_name             = var.resource_group_name
  storage_account_name            = data.azurerm_storage_account.storage.name
  storage_account_access_key      = data.azurerm_storage_account.storage.primary_access_key
  service_plan_id                 = var.create_service_plan ? azurerm_service_plan.service_plan[0].id : var.service_plan_id
  https_only                      = true
  tags                            = var.tags
  key_vault_reference_identity_id = var.user_assigned_identity_id

  identity {
    type         = var.user_assigned_identity_id != null ? "SystemAssigned, UserAssigned" : "SystemAssigned"
    identity_ids = var.user_assigned_identity_id != null ? [var.user_assigned_identity_id] : null
  }

  app_settings = local.app_settings

  site_config {
    application_insights_connection_string = one(azurerm_application_insights.logging[*].connection_string)
    application_insights_key               = one(azurerm_application_insights.logging[*].instrumentation_key)

    application_stack {
      python_version = "3.11"
    }
  }

  lifecycle {
    ignore_changes = [
      tags["hidden-link: /app-insights-conn-string"],
      tags["hidden-link: /app-insights-instrumentation-key"],
      tags["hidden-link: /app-insights-resource-id"]
    ]
  }
}

data "azurerm_client_config" "current" {
}

locals {
  func_base_url = "https://${azurerm_linux_function_app.function.default_hostname}"
}

data "archive_file" "function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/functions"
  output_path = "${path.module}/functions.zip"
  excludes    = ["local.settings.json", ".venv", ".vscode", "__pycache__"]
}

data "azurerm_linux_function_app" "func" {
  name                = coalesce(var.func_resource_name, local.func_name)
  resource_group_name = var.resource_group_name
}

# Deploy using function tools
resource "null_resource" "deploy" {
  count = var.func_app_msi_exists && var.do_deploy ? 1 : 0

  triggers = {
    requirements_md5 = data.archive_file.function_zip.output_md5
    func_id          = data.azurerm_linux_function_app.func.id
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    working_dir = path.module
    command     = "./deploy-local.sh"

    environment = {
      AZ_CLI_LOGIN_ARGS       = var.az_cli_login_args
      FUNC_NAME               = azurerm_linux_function_app.function.name
      SUBSCRIPTION_ID         = data.azurerm_client_config.current.subscription_id
      DEPLOY_FROM_WORKING_DIR = var.deploy_from_working_dir
    }
  }
}
