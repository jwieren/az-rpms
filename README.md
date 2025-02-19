# Module resource-power-mgmt

This terraform module will deploy a function app that can manage power
states for various Azure resources.  Current supported resources:

* Virtual machines
* Azure Postgres databases
* Application Gateways
* Storage account SFTP feature

In addition, a workbook is deployed that provides a useful dashboard 
for monitoring.  

## Usage

Power management is managed by tags on the above resources. 

Tags can be specified in one of two ways.  The easiest most simple approach is to use tag `Schedule`. 
Alternatively you can specify separate `PowerOn` and `PowerOff` values

### Schedule

These are predefined values that mean the following:

| `Schedule` value      | Meaning                                        |
| --------------------- | ---------------------------------------------- |
| BusinessHours         |  8am - 6pm, Monday to Friday                   |
| BusinessHoursExtended |  8am - 10pm, Monday to Friday                  |
| AlwaysOn              |  Turn resources on if not running, leave on    |
| AlwaysOff             |  Turn resource off if running, leave off       |
| OffAtMidnight         |  If a resource is running, turn off at 12:00am |
| None                  |  Ignore from power management                  |

### PowerOn and PowerOff

These can take two forms:

1. A comma separated value string, of times for each day of the week, to 5 minute resolution.  `-` or `*` indicate a day when no action should occur.

e.g.  `PowerOn=7:05,7:05,7:05,7:05,7:05,10,-` means power a resource on at 7:05am on Mon-Friday, 10am on Saturday, and take no action on Sunday.

2. A cron expression.

e.g. `PowerOn=0 8 * * *` means power a resource on at 8am every day.

When a `PowerOn` and `PowerOff` pair are combined together, you can create
fairly specific rules for your application.

e.g. `PowerOn=45 7,16 * * * PowerOff=15 8,17 * * *` means power on a resource at 7:45am and 4:45pm, and power it off at 8:15am and 5:15pm every day.

## Customisation

### Adding or modifying values for tag `Schedule`

To add additional values for Schedule, search for `POWER_SCHEDULE_CONFIGS`


<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5 |
| <a name="requirement_archive"></a> [archive](#requirement\_archive) | ~> 2.0 |
| <a name="requirement_azurerm"></a> [azurerm](#requirement\_azurerm) | >= 3.0 |
| <a name="requirement_null"></a> [null](#requirement\_null) | ~> 3.0 |
| <a name="requirement_random"></a> [random](#requirement\_random) | ~> 3.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_archive"></a> [archive](#provider\_archive) | 2.7.0 |
| <a name="provider_azurerm"></a> [azurerm](#provider\_azurerm) | 4.19.0 |
| <a name="provider_null"></a> [null](#provider\_null) | 3.2.3 |
| <a name="provider_random"></a> [random](#provider\_random) | 3.6.3 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [azurerm_application_insights.logging](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/application_insights) | resource |
| [azurerm_application_insights_workbook.workbook](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/application_insights_workbook) | resource |
| [azurerm_linux_function_app.function](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/linux_function_app) | resource |
| [azurerm_service_plan.service_plan](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/service_plan) | resource |
| [azurerm_storage_queue.queues](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_queue) | resource |
| [null_resource.deploy](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [random_id.id](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/id) | resource |
| [random_uuid.insights_workbook](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid) | resource |
| [archive_file.function_zip](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file) | data source |
| [azurerm_client_config.current](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/client_config) | data source |
| [azurerm_linux_function_app.func](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/linux_function_app) | data source |
| [azurerm_storage_account.storage](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/storage_account) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_insights_name"></a> [app\_insights\_name](#input\_app\_insights\_name) | The name of the app insights instance | `string` | `null` | no |
| <a name="input_app_insights_workspace_id"></a> [app\_insights\_workspace\_id](#input\_app\_insights\_workspace\_id) | The name of the app insights workspace | `string` | `null` | no |
| <a name="input_az_cli_login_args"></a> [az\_cli\_login\_args](#input\_az\_cli\_login\_args) | Login args to pass to azure cli | `string` | `null` | no |
| <a name="input_create_app_insights"></a> [create\_app\_insights](#input\_create\_app\_insights) | Whether to create app insights for this func | `bool` | `true` | no |
| <a name="input_create_service_plan"></a> [create\_service\_plan](#input\_create\_service\_plan) | Whether to create a service plan. | `bool` | `true` | no |
| <a name="input_deploy_from_working_dir"></a> [deploy\_from\_working\_dir](#input\_deploy\_from\_working\_dir) | The entry point directory to function app code | `string` | n/a | yes |
| <a name="input_do_deploy"></a> [do\_deploy](#input\_do\_deploy) | Whether to deploy the function app. Requires azure function tools to be installed. | `bool` | `true` | no |
| <a name="input_func_app_msi_exists"></a> [func\_app\_msi\_exists](#input\_func\_app\_msi\_exists) | The azurerm\_linux\_function\_app resource has a bug where the output 'identity' is null until after the SystemAssigned identity is created.  This means you cannot use the output for an rbac assignment until<br>after the function has been created.  Set this variable to true *after* the identity has been created to assign the function app to the storage account. | `bool` | `true` | no |
| <a name="input_func_name"></a> [func\_name](#input\_func\_name) | The name of the function app | `string` | `"Alerts"` | no |
| <a name="input_func_resource_name"></a> [func\_resource\_name](#input\_func\_resource\_name) | The full name of the function app resource | `string` | n/a | yes |
| <a name="input_location"></a> [location](#input\_location) | The location | `string` | n/a | yes |
| <a name="input_mg_scopes"></a> [mg\_scopes](#input\_mg\_scopes) | The scope of management groups to manage powerOff/powerOn. | `list(string)` | `null` | no |
| <a name="input_queue_updatemgmt_events"></a> [queue\_updatemgmt\_events](#input\_queue\_updatemgmt\_events) | The name of the queue to create a subscription over maintenance window topics. These are consumed to handle updatemgmt events. | `string` | n/a | yes |
| <a name="input_resource_group_name"></a> [resource\_group\_name](#input\_resource\_group\_name) | The resource group | `string` | n/a | yes |
| <a name="input_resourcegraph_mg_scope"></a> [resourcegraph\_mg\_scope](#input\_resourcegraph\_mg\_scope) | The scope of resource graph to search under. | `string` | n/a | yes |
| <a name="input_service_plan_id"></a> [service\_plan\_id](#input\_service\_plan\_id) | The id of the service plan, if not creating it here. | `string` | `null` | no |
| <a name="input_service_plan_name"></a> [service\_plan\_name](#input\_service\_plan\_name) | The name of the service plan, if creating it here. | `string` | `null` | no |
| <a name="input_storage_account_name"></a> [storage\_account\_name](#input\_storage\_account\_name) | The storage account name | `string` | n/a | yes |
| <a name="input_sub_scopes"></a> [sub\_scopes](#input\_sub\_scopes) | A list of sub\_ids to manage powerOff/powerOn. Takes precedence over mg\_scopes if specified | `list(string)` | `null` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | Tags to accompany tagable resources | `map(any)` | n/a | yes |
| <a name="input_user_assigned_identity_client_id"></a> [user\_assigned\_identity\_client\_id](#input\_user\_assigned\_identity\_client\_id) | The UAI to assign to the app | `string` | `null` | no |
| <a name="input_user_assigned_identity_id"></a> [user\_assigned\_identity\_id](#input\_user\_assigned\_identity\_id) | The UAI to assign to the app | `string` | `null` | no |
| <a name="input_workbook_more_info_url"></a> [workbook\_more\_info\_url](#input\_workbook\_more\_info\_url) | A link in the workbook for more info | `string` | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_func_base_url"></a> [func\_base\_url](#output\_func\_base\_url) | n/a |
| <a name="output_func_id"></a> [func\_id](#output\_func\_id) | n/a |
| <a name="output_func_name"></a> [func\_name](#output\_func\_name) | n/a |
| <a name="output_managed_identity_principal_id"></a> [managed\_identity\_principal\_id](#output\_managed\_identity\_principal\_id) | n/a |
| <a name="output_verify_url"></a> [verify\_url](#output\_verify\_url) | n/a |
<!-- END_TF_DOCS -->