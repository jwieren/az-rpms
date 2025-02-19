variable "resource_group_name" {
  type        = string
  description = "The resource group"
}

variable "location" {
  type        = string
  description = "The location"
}

variable "func_name" {
  description = "The name of the function app"
  type        = string
  default     = "Alerts"
}

variable "storage_account_name" {
  description = "The storage account name"
  type        = string
}

variable "create_service_plan" {
  type        = bool
  default     = true
  description = "Whether to create a service plan."
}

variable "service_plan_name" {
  type        = string
  description = "The name of the service plan, if creating it here."
  default     = null
}

variable "service_plan_id" {
  type        = string
  description = "The id of the service plan, if not creating it here."
  default     = null
}

variable "tags" {
  type        = map(any)
  description = "Tags to accompany tagable resources"
}

variable "app_insights_name" {
  type        = string
  description = "The name of the app insights instance"
  default     = null
}

variable "app_insights_workspace_id" {
  type        = string
  description = "The name of the app insights workspace"
  default     = null
}

variable "create_app_insights" {
  type        = bool
  description = "Whether to create app insights for this func"
  default     = true
}

variable "func_resource_name" {
  description = "The full name of the function app resource"
  type        = string
}

variable "user_assigned_identity_id" {
  description = "The UAI to assign to the app"
  type        = string
  default     = null
}

variable "user_assigned_identity_client_id" {
  description = "The UAI to assign to the app"
  type        = string
  default     = null
}

variable "resourcegraph_mg_scope" {
  description = "The scope of resource graph to search under."
  type        = string
}

variable "mg_scopes" {
  description = "The scope of management groups to manage powerOff/powerOn."
  default     = null
  type        = list(string)

  validation {
    condition     = alltrue(var.mg_scopes != null ? [for mg_id in var.mg_scopes : can(regex("^[a-zA-Z0-9-_().]+$", mg_id))] : [true])
    error_message = "Only valid management group names are accepted. [${join(",", var.mg_scopes != null ? var.mg_scopes : ["null"])}] was provided."
  }
}

variable "sub_scopes" {
  description = "A list of sub_ids to manage powerOff/powerOn. Takes precedence over mg_scopes if specified"
  default     = null
  type        = list(string)

  validation {
    condition = try(
      alltrue([for sub_id in var.sub_scopes : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$", sub_id))]),
      var.sub_scopes == null
    )
    error_message = "Only valid UUIDs are accepted"
  }
}

variable "deploy_from_working_dir" {
  type        = string
  description = "The entry point directory to function app code"
}

variable "func_app_msi_exists" {
  type        = bool
  description = <<-EOS
  The azurerm_linux_function_app resource has a bug where the output 'identity' is null until after the SystemAssigned identity is created.  This means you cannot use the output for an rbac assignment until
  after the function has been created.  Set this variable to true *after* the identity has been created to assign the function app to the storage account.
EOS
  default     = true
}

variable "do_deploy" {
  type        = bool
  description = "Whether to deploy the function app. Requires azure function tools to be installed."
  default     = true
}

variable "az_cli_login_args" {
  type        = string
  description = "Login args to pass to azure cli"
  default     = null
}

variable "queue_updatemgmt_events" {
  type        = string
  description = "The name of the queue to create a subscription over maintenance window topics. These are consumed to handle updatemgmt events."
}

variable "workbook_more_info_url" {
  type        = string
  description = "A link in the workbook for more info"
}