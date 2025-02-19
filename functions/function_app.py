"""Azure function app entry point for resource power management"""

import os
import logging
from typing import List
from functools import partial, lru_cache

from pydantic import ValidationError
import azure.functions as func
from azure.core.exceptions import HttpResponseError

from helpers import (
    SubscriptionCheck,
    convert_time_schedule_to_cron,
    decode_resource_id,
    find_matching_alert_ids,
    is_public_holiday,
    is_valid_powertag_value,
    is_valid_schedule_value,
    log_resource_event,
    nzt_now_str,
    should_process_cron_event,
)

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from azure_helpers.managers.azuremanager import AzureManager, AzureResource
from azure_helpers.managers.vm import VM, VMManager
from azure_helpers.managers.agw import AppGatewayManager
from azure_helpers.managers.pgsql import PostgreSQLManager
from azure_helpers.managers.sa_sftp import StorageAccountSFTPManager
from azure_helpers.mg import ManagementGroupsManager
from azure_helpers.queue import QueueManager
from azure_helpers.resourcegraph import ResourceGraphManager
from azure_helpers.alerts import AlertsManager
from azure_helpers.util import (
    DeferredWaitPowerActionCheck,
    ResourceGraphItem,
    ResourcePowerAction,
    Subscription,
    PowerMgmtRetryableError,
    ThrottledRetryableError,
)
from azure_helpers.credential import CachedCredential


from azure.core.exceptions import ResourceNotFoundError
from tenacity import retry, wait_exponential, retry_if_exception_type, stop_after_delay

# Supported resource types. If you don't want to enable all types, specify the ones you do 
# want as a csv in env var TYPES_ENABLED
FEATURE_APPGATEWAY = "agw"
FEATURE_VM = "vm"
FEATURE_PSQL = "pqsl"
FEATURE_SFTP = "sftp"

SUPPORTED_TYPES = ",".join([FEATURE_APPGATEWAY, FEATURE_VM, FEATURE_PSQL, FEATURE_SFTP])


# There are two ways to specify a power schedule.  You can either provide:
# - a schedule name
# - a pair of PowerOn or PowerOff values.
RESOURCE_TAGKEY_SCHEDULE = "Schedule"
RESOURCE_TAGKEY_POWERON = "PowerOn"
RESOURCE_TAGKEY_POWEROFF = "PowerOff"
RESOURCE_TAGKEY_POWERMGMT_FEATURE_SUBTYPE = "PowerMgmtFeature"

# Set to any value to disable this VM from being targetted
RESOURCE_TAGKEY_POWERMGMT_EXEMPT = "_POWERMGMT_EXEMPT_"

# These are tag values that are used to report the status by this function
RESOURCE_TAGKEY_POWERMGMT = "_POWERMGMT_STATUS_"
RESOURCE_TAGKEY_POWERMGMT_TIME = "_POWERMGMT_LASTUPDATED_"
RESOURCE_TAGVAL_START = "Auto-started"
RESOURCE_TAGVAL_START_IGNORED = "Auto-start ignored"
RESOURCE_TAGVAL_STOP = "Auto-stopped"
RESOURCE_TAGVAL_STOP_IGNORED = "Auto-stop ignored"
RESOURCE_TAGVAL_STARTED_FOR_MAINTENANCE = "In maintenance window (was stopped)"
RESOURCE_TAGVAL_IN_MAINTENANCE_STOP_PENDING = "In maintenance window (stop pending)"
RESOURCE_TAGVAL_IN_MAINTENANCE = "In maintenance window"
RESOURCE_TAGVAL_INFO_POST_MAINTENANCE = "Maintenance window completed"
RESOURCE_TAGVAL_STOP_POST_MAINTENANCE = "Auto-stopped (post maintenance)"

RESOURCE_TAGVALS_IN_MAINTENANCE_WINDOW = [
    RESOURCE_TAGVAL_STARTED_FOR_MAINTENANCE,
    RESOURCE_TAGVAL_IN_MAINTENANCE_STOP_PENDING,
    RESOURCE_TAGVAL_IN_MAINTENANCE,
]

RESOURCE_ACTION_START = "start"
RESOURCE_ACTION_START_PRE_UPDATES = "start_for_updates"
RESOURCE_ACTION_STOP = "stop"
RESOURCE_ACTION_STOP_POST_UPDATES = "stop_post_updates"

POWER_MGMT_TAGS = [
    RESOURCE_TAGKEY_POWERON,
    RESOURCE_TAGKEY_POWEROFF,
    RESOURCE_TAGKEY_SCHEDULE,
]

DEFERRED_CHECK_MAX_RETRIES = 10
IGNORE_PUBLIC_HOLIDAYS = "NO_POWERON_ON_PUBLIC_HOLIDAYS"

# These power schedules are compared as case insensitive
POWER_SCHEDULE_CONFIGS = {
    # This starts resources at 8am and shuts then down at 6pm Mondays - Fridays,
    # except public holidays
    "businesshours": {
        RESOURCE_TAGKEY_POWERON: "0 8 * * 1,2,3,4,5",
        RESOURCE_TAGKEY_POWEROFF: "0 18 * * 1,2,3,4,5",
        IGNORE_PUBLIC_HOLIDAYS: True,
    },
    # This starts resources at 8am and shuts then down at 10pm Mondays - Fridays,
    "businesshoursextended": {
        RESOURCE_TAGKEY_POWERON: "0 8 * * 1,2,3,4,5",
        RESOURCE_TAGKEY_POWEROFF: "0 22 * * 1,2,3,4,5",
    },
    # This will turn off any resources if they’re ever running, checking every N hours.
    # The value is customisable by env var ALWAYSOFF_DELAY
    # (used mainly when e.g. development is paused for a time, or a resource is disabled for some time.)
    "alwaysoff": {
        RESOURCE_TAGKEY_POWERON: None,
        RESOURCE_TAGKEY_POWEROFF: f"0 */{os.getenv('ALWAYSOFF_DELAY', '4')} * * *",
    },
    # This should be applied to resources that are expected to be running 24/7. Check once every hour.
    "alwayson": {
        RESOURCE_TAGKEY_POWERON: "0 * * * *",
        RESOURCE_TAGKEY_POWEROFF: None,
    },
    # This will leave the instance as-is, but will shut it down at midnight if it’s left on.  This is useful for things that are needed on an ad-hoc basis, but generally should be off.
    "offatmidnight": {
        RESOURCE_TAGKEY_POWERON: None,
        RESOURCE_TAGKEY_POWEROFF: "0 0 * * *",
    },
    # This will leave the instance as-is
    "none": {RESOURCE_TAGKEY_POWERON: None, RESOURCE_TAGKEY_POWEROFF: None},
}


def create_powermgmt_tags(text: str) -> dict:
    return {
        RESOURCE_TAGKEY_POWERMGMT: text,
        RESOURCE_TAGKEY_POWERMGMT_TIME: nzt_now_str(),
    }


#
# Infuriatingly this seems to be required to suppress http tracing when running locally.
# When running in Azure, the option '"enableLiveMetricsFilters": true' does the trick
#
log_names = [
    "azure.core.pipeline.policies.http_logging_policy",
    "azure.identity._credentials.managed_identity",
    "azure.identity._credentials.app_service",
    "azure.identity._credentials.default",
    "azure.identity._internal.decorators",
    "azure.identity._credentials.chained",
]
for name in log_names:
    logger = logging.getLogger(name)
    logger.setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def get_cached_credentials() -> DefaultAzureCredential | ManagedIdentityCredential:
    return CachedCredential(client_id=os.getenv("CLIENT_ID", None)).get()


retryable_args = {
    "retry": retry_if_exception_type(
        [PowerMgmtRetryableError, ThrottledRetryableError]
    ),
    "stop": stop_after_delay(30),
    "wait": wait_exponential(multiplier=1, min=2, max=6),
    "reraise": True,
}


@retry(**retryable_args)
def start_with_retry(manager: AzureManager, resource: AzureResource, **kwargs):
    return manager.start(resource, **kwargs)


@retry(**retryable_args)
def stop_with_retry(manager: AzureManager, resource: AzureResource, **kwargs):
    return manager.stop(resource, **kwargs)


@retry(**retryable_args)
def add_tags_with_retry(
    manager: AzureManager, resource: AzureResource, tags: dict
) -> bool | None:
    return manager.add_tags(resource, tags)


@retry(**retryable_args)
def get_tagged_resources_with_retry(
    manager: AzureManager, tags: List[str], **kwargs
) -> List[AzureResource]:
    return manager.get_tagged_resources(tags, **kwargs)


app = func.FunctionApp()


@app.function_name(name="verify")
@app.route(route="verify", methods=["post"], auth_level="anonymous")
# pylint: disable=unused-argument
def verify_function(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    return func.HttpResponse("OK!")


TRIGGER_INTERVAL_MINS = int(os.getenv("TRIGGER_INTERVAL", "5"))
TRIGGER_CRON = f"*/{TRIGGER_INTERVAL_MINS} * * * *"


@app.timer_trigger(
    schedule=TRIGGER_CRON,
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
# pylint: disable=invalid-name
def timer_trigger(myTimer: func.TimerRequest) -> None:
    schedule_status = myTimer.schedule_status

    if (sub_scopes := os.getenv("SUB_SCOPES")) is not None:
        for sub_id in sub_scopes.split(","):
            subs = enqueue_sub_checks(
                enqueue_queue_name=os.getenv("QUEUE_PROCESS_SUBS"),
                sub_id=sub_id,
                last_check=schedule_status["Last"],
            )
            logging.info(
                "Timer trigger fired (%s), discovered %s subs to enqueue checks for. schedule=%s",
                TRIGGER_CRON,
                len(subs),
                schedule_status,
            )

    elif (mg_scopes := os.getenv("MG_SCOPES")) is not None:
        for mg in mg_scopes.split(","):
            subs = enqueue_sub_checks(
                enqueue_queue_name=os.getenv("QUEUE_PROCESS_SUBS"),
                mg_id=mg,
                last_check=schedule_status["Last"],
            )
            logging.info(
                "Timer trigger fired (%s), discovered %s to enqueue checks in mg '%s' to enqueue checks for. schedule=%s",
                TRIGGER_CRON,
                len(subs),
                mg,
                schedule_status,
            )


@app.function_name(name="trigger")
@app.route(route="trigger", methods=["get"], auth_level="function")
# pylint: disable=unused-argument
def trigger_function(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:

    subs = enqueue_sub_checks(
        enqueue_queue_name=os.getenv("QUEUE_PROCESS_SUBS"),
        mg_id=req.params.get("management_group_id"),
        sub_id=req.params.get("subscription_id"),
    )

    if len(subs) == 0:
        return func.HttpResponse("No subs found matching criteria", status_code=404)

    return func.HttpResponse(
        f"Triggered checks for {len(subs)} sub(s)", status_code=200
    )


@app.queue_trigger(
    arg_name="msg",
    queue_name="%QUEUE_PROCESS_SUBS%",
    connection="AzureWebJobsStorage",
)
def process_sub_from_queue(msg: func.QueueMessage, context: func.Context):

    try:
        logging.debug("Processing message: %s", msg.get_body().decode("utf-8"))
        sub_check = SubscriptionCheck.model_validate_json(
            msg.get_body().decode("utf-8")
        )
    except ValidationError as e:
        logging.error("Couldn't process message: %s", str(e))
        raise

    try:
        resourcegraph_manager = ResourceGraphManager(
            get_cached_credentials(), subscriptions=[sub_check.id]
        )
        all_alerts: List[ResourceGraphItem] = (
            resourcegraph_manager.query_metric_alerts()
        )

        types_enabled = os.getenv("TYPES_ENABLED", SUPPORTED_TYPES)
        supported_res_types = []

        if FEATURE_VM in types_enabled:
            vm_manager = VMManager(sub_check.id, get_cached_credentials())
            supported_res_types.append(
                {
                    "manager": vm_manager,
                    "resources": get_tagged_resources_with_retry(
                        vm_manager, POWER_MGMT_TAGS
                    ),
                }
            )

        if FEATURE_APPGATEWAY in types_enabled:
            agw_manager = AppGatewayManager(sub_check.id, get_cached_credentials())
            supported_res_types.append(
                {
                    "manager": agw_manager,
                    "resources": get_tagged_resources_with_retry(
                        agw_manager, POWER_MGMT_TAGS
                    ),
                }
            )

        if FEATURE_PSQL in types_enabled:
            pgsql_manager = PostgreSQLManager(sub_check.id, get_cached_credentials())
            supported_res_types.append(
                {
                    "manager": pgsql_manager,
                    "resources": get_tagged_resources_with_retry(
                        pgsql_manager, POWER_MGMT_TAGS
                    ),
                }
            )

        if FEATURE_SFTP in types_enabled:
            sa_sftp_manager = StorageAccountSFTPManager(
                sub_check.id, get_cached_credentials()
            )
            supported_res_types.append(
                {
                    "manager": sa_sftp_manager,
                    "resources": get_tagged_resources_with_retry(
                        sa_sftp_manager,
                        POWER_MGMT_TAGS,
                        additional_required_tags={
                            RESOURCE_TAGKEY_POWERMGMT_FEATURE_SUBTYPE: FEATURE_SFTP
                        },
                    ),
                    "sub_type": FEATURE_SFTP,
                }
            )

    except Exception as e:
        logging.exception("Could not process sub: %s", e)
        raise e

    for res_type in supported_res_types:
        mgr = res_type.get("manager")
        for res in res_type.get("resources"):

            if (schedule_value := res.tags.get(RESOURCE_TAGKEY_SCHEDULE)) is not None:
                schedule = POWER_SCHEDULE_CONFIGS.get(schedule_value.casefold())

                if schedule:
                    power_off = schedule[RESOURCE_TAGKEY_POWEROFF]
                    power_on = schedule[RESOURCE_TAGKEY_POWERON]

                    is_holiday, holiday_name = is_public_holiday()
                    if schedule.get(IGNORE_PUBLIC_HOLIDAYS, False) and is_holiday:
                        logger.info(
                            "Ignoring power on for %s, public holiday '%s'",
                            res.name,
                            holiday_name,
                        )
                        power_on = None

                else:
                    msg = f"{res.resource_type} {res.name} has invalid tag value '{schedule_value}' for {RESOURCE_TAGKEY_SCHEDULE}"
                    log_resource_event(res, "tagcheck", result="failed", debug=msg)

                    mgr.add_tags(
                        res,
                        create_powermgmt_tags(msg),
                    )
                    continue
            else:
                power_off = res.tags.get(RESOURCE_TAGKEY_POWEROFF)
                power_on = res.tags.get(RESOURCE_TAGKEY_POWERON)

                if power_off and not is_valid_powertag_value(power_off):
                    msg = f"{res.resource_type} {res.name} has invalid tag value '{power_off}' for {RESOURCE_TAGKEY_POWEROFF}"
                    log_resource_event(res, "tagcheck", result="failed", debug=msg)
                    mgr.add_tags(
                        res,
                        create_powermgmt_tags(msg),
                    )
                    continue

                if power_on and not is_valid_powertag_value(power_on):
                    msg = f"{res.resource_type} {res.name} has invalid tag value '{power_on}' for {RESOURCE_TAGKEY_POWERON}"
                    log_resource_event(res, "tagcheck", result="failed", debug=msg)
                    mgr.add_tags(
                        res,
                        create_powermgmt_tags(msg),
                    )
                    continue

            logging.debug(
                "Found %s %s in state %s, tags: %s|%s",
                res.resource_type,
                res.id,
                res.power_state,
                power_off,
                power_on,
            )

            if res.tags.get(RESOURCE_TAGKEY_POWERMGMT_EXEMPT) or (
                power_off is None and power_on is None
            ):
                logging.info(
                    "%s %s opts out of power management",
                    res.resource_type,
                    res.id,
                )
                continue

            # Convert any time schedules to cron expressions to evaluate in a single function.
            # Any schedule that does not apply to the current day will be handled as 'None'
            if power_off and is_valid_schedule_value(power_off):
                power_off = convert_time_schedule_to_cron(
                    res.name, power_off, "deallocate"
                )

            if power_on and is_valid_schedule_value(power_on):
                power_on = convert_time_schedule_to_cron(
                    res.name, power_on, RESOURCE_ACTION_START
                )

            last_check = sub_check.last_check

            if os.getenv("RESET_LAST_INVOCATION", "false") == "true":
                # Normally a cron event is only processed if it occured between
                # the previous run and the current time.  This variable will
                # assume no previous run has taken place. This should really only
                # be used fortesting scenarios, or catching up after failure.
                logging.warning(
                    "Not considering last invocation time during cronchecks."
                )
                last_check = None

            if power_off and res.in_stoppable_state():
                if should_process_cron_event(
                    last_check,
                    res,
                    power_off,
                    "deallocate",
                    power_on,
                    RESOURCE_ACTION_START,
                    correlation_id=context.invocation_id,
                ):
                    enqueue_power_action(
                        enqueue_queue_name=os.getenv("QUEUE_POWERMGMT_EVENTS"),
                        power_action=res.create_power_action(
                            RESOURCE_ACTION_STOP,
                            find_matching_alert_ids(
                                all_alerts, res.id, res.resource_type
                            ),
                            res_type.get("sub_type", None),
                        ),
                    )

            if power_on and res.in_startable_state():
                if should_process_cron_event(
                    last_check,
                    res,
                    power_on,
                    RESOURCE_ACTION_START,
                    power_off,
                    "deallocate",
                    correlation_id=context.invocation_id,
                ):
                    enqueue_power_action(
                        enqueue_queue_name=os.getenv("QUEUE_POWERMGMT_EVENTS"),
                        power_action=res.create_power_action(
                            RESOURCE_ACTION_START,
                            find_matching_alert_ids(
                                all_alerts, res.id, res.resource_type
                            ),
                            res_type.get("sub_type", None),
                        ),
                    )

    return


def create_manager_by_type(
    sub_id, resource_type, sub_type=None
) -> AppGatewayManager | VMManager | PostgreSQLManager | StorageAccountSFTPManager:
    if resource_type == "microsoft.network/applicationgateways":
        return AppGatewayManager(sub_id, get_cached_credentials())
    elif resource_type == "microsoft.compute/virtualmachines":
        return VMManager(sub_id, get_cached_credentials())
    elif resource_type == "microsoft.dbforpostgresql/flexibleservers":
        return PostgreSQLManager(sub_id, get_cached_credentials())
    elif resource_type == "microsoft.storage/storageaccounts" and sub_type == "sftp":
        return StorageAccountSFTPManager(sub_id, get_cached_credentials())

    raise ValueError(f"Unhandled resource type {resource_type}")


def handle_powermgmt_event_with_deferred_wait(event: ResourcePowerAction):
    action = event.action
    manager = create_manager_by_type(event.sub_id, event.resource_type, event.sub_type)
    resource = manager.get(event.resource_group, event.name)
    alerts_manager = AlertsManager(event.sub_id, get_cached_credentials())

    check_args = {
        "id": event.id,
        "sub_type": event.sub_type,
        "action": event.action,
        "created_at": event.created_at,
        "alert_ids": event.alert_ids,
        "attempt_num": event.attempt_num,
        "tag_text": "",
        "wait_retries": 0,
    }

    try:
        suppress_alerts = partial(
            alerts_manager.update_alert_processing_rule,
            resource.id,
            f"AzAPR_{resource.name}_disableactiongroup",
            event.resource_group,
        )

        if action == RESOURCE_ACTION_START:
            # Resource OK to be started
            if resource.in_startable_state():
                check_args["tag_text"] = RESOURCE_TAGVAL_START
                check_args["continuation_token"] = start_with_retry(
                    manager, resource, reason="scheduled", return_poller=True
                )
                enqueue_deferred_check_action(
                    DeferredWaitPowerActionCheck(**check_args)
                )

        elif action == RESOURCE_ACTION_START_PRE_UPDATES:
            # Resource OK to be started prior to maintenance window
            if resource.in_startable_state():
                check_args["tag_text"] = RESOURCE_TAGVAL_STARTED_FOR_MAINTENANCE
                check_args["continuation_token"] = start_with_retry(
                    manager,
                    resource,
                    reason="pre-maintenance window",
                    return_poller=True,
                )
                enqueue_deferred_check_action(
                    DeferredWaitPowerActionCheck(**check_args)
                )
            # Resource is already started. Add a tag to note it shouldn't be shut-down after!
            else:
                logging.info(
                    "Updating tag on %s as it is maintenance window",
                    resource.name,
                )
                add_tags_with_retry(
                    manager,
                    resource,
                    create_powermgmt_tags(RESOURCE_TAGVAL_IN_MAINTENANCE),
                )

        elif action == RESOURCE_ACTION_STOP:
            if event.alert_ids:
                suppress_alerts(True)

            # Resource OK to be stopped
            if resource.in_stoppable_state():
                # Only stop if the resource isn't in its maintenance config window
                if (
                    resource.tags.get(RESOURCE_TAGKEY_POWERMGMT, "")
                    not in RESOURCE_TAGVALS_IN_MAINTENANCE_WINDOW
                ):
                    # Flexiserver needs tags updated before stopping
                    if (
                        resource.resource_type
                        == "microsoft.dbforpostgresql/flexibleservers"
                    ):
                        add_tags_with_retry(
                            manager,
                            resource,
                            create_powermgmt_tags(RESOURCE_TAGVAL_STOP),
                        )
                    # App gateway can't handle updating tags when stopping, as it triggers
                    # a reprovision that will start it again...
                    elif (
                        resource.resource_type
                        == "microsoft.network/applicationgateways"
                    ):
                        pass
                    else:
                        check_args["tag_text"] = RESOURCE_TAGVAL_STOP

                    check_args["continuation_token"] = stop_with_retry(
                        manager, resource, reason="scheduled", return_poller=True
                    )
                    enqueue_deferred_check_action(
                        DeferredWaitPowerActionCheck(**check_args)
                    )
                # Resource should be kept running due to maintenance window.
                # Note: This is critical to add as the tag value is used to stop the machine at conclusion
                else:
                    logging.info(
                        "Ignoring stop action on %s as it is maintenance window",
                        resource.name,
                    )
                    add_tags_with_retry(
                        manager,
                        resource,
                        create_powermgmt_tags(
                            RESOURCE_TAGVAL_IN_MAINTENANCE_STOP_PENDING
                        ),
                    )

        elif action == RESOURCE_ACTION_STOP_POST_UPDATES:
            if event.alert_ids:
                suppress_alerts(True)

            # Only deallocate if a stop was received during the maintenance config window,
            # or the VM was stopped to begin with
            if resource.in_stoppable_state() and resource.tags.get(
                RESOURCE_TAGKEY_POWERMGMT, ""
            ) in [
                RESOURCE_TAGVAL_IN_MAINTENANCE_STOP_PENDING,
                RESOURCE_TAGVAL_STARTED_FOR_MAINTENANCE,
            ]:
                check_args["tag_text"] = RESOURCE_TAGVAL_STOP_POST_MAINTENANCE
                check_args["continuation_token"] = stop_with_retry(
                    manager,
                    resource,
                    reason="post-maintenance window",
                    return_poller=True,
                )
                enqueue_deferred_check_action(
                    DeferredWaitPowerActionCheck(**check_args)
                )

            # Add informational tags indicating the maintenance window is completed
            else:
                add_tags_with_retry(
                    manager,
                    resource,
                    create_powermgmt_tags(RESOURCE_TAGVAL_INFO_POST_MAINTENANCE),
                )

        else:
            raise AssertionError(f"Invalid action {action}")

    except Exception as e:
        logging.exception("Could not %s %s: %s.", action, resource.name, e)
        raise


@app.queue_trigger(
    arg_name="msg",
    queue_name="%QUEUE_POWERMGMT_EVENTS%",
    connection="AzureWebJobsStorage",
)
def process_powermgmt_event(msg: func.QueueMessage):
    try:
        logging.debug("Processing power event: %s", msg.get_body().decode("utf-8"))
        event = ResourcePowerAction.model_validate_json(msg.get_body().decode("utf-8"))
    except ValidationError as e:
        logging.error("Couldn't process message: %s", str(e))
        raise

    handle_powermgmt_event_with_deferred_wait(event)


def check_powermgmt_status(event: DeferredWaitPowerActionCheck):
    action = event.action
    manager = create_manager_by_type(event.sub_id, event.resource_type, event.sub_type)
    resource = manager.get(event.resource_group, event.name)
    alerts_manager = AlertsManager(event.sub_id, get_cached_credentials())

    suppress_alerts = partial(
        alerts_manager.update_alert_processing_rule,
        resource.id,
        f"AzAPR_{resource.name}_disableactiongroup",
        event.resource_group,
    )

    try:
        if (
            action == RESOURCE_ACTION_STOP
            or action == RESOURCE_ACTION_STOP_POST_UPDATES
        ):
            result = manager.stop(
                resource, continuation_token=event.continuation_token, timeout=1
            )
        elif (
            action == RESOURCE_ACTION_START
            or action == RESOURCE_ACTION_START_PRE_UPDATES
        ):
            result = manager.start(
                resource, continuation_token=event.continuation_token, timeout=1
            )
        else:
            raise ValueError(f"Unhandled action {action}")
    except HttpResponseError as e:
        logging.info("Failed to check status on %s: %s", resource, e)
        result = "failed"

    if result == "succeeded" and event.tag_text != "":
        # If we fail to update tags, treat as another wait
        if not add_tags_with_retry(
            manager, resource, create_powermgmt_tags(event.tag_text)
        ):
            logging.info(
                "Action %s on %s succeeded, but updating tags to %s failed.",
                action,
                resource,
                event.tag_text,
            )
            result = "inprogress"

    if result == "succeeded":
        # re-enable alerts
        if action == RESOURCE_ACTION_START and event.alert_ids:
            suppress_alerts(False)

    elif result == "inprogress":
        event.wait_retries = event.wait_retries + 1

        if event.wait_retries < DEFERRED_CHECK_MAX_RETRIES:
            enqueue_deferred_check_action(event)
        else:
            result = "timeout"

    log_resource_event(
        resource,
        "checkPowerMgmtStatus",
        result=result,
        **{
            "action": event.action,
            "tag_text": event.tag_text,
            "attempt_num": event.attempt_num,
            "wait_retries": event.wait_retries,
        },
    )


@app.queue_trigger(
    arg_name="msg",
    queue_name="%QUEUE_POWERMGMT_DEFERRED_CHECKS%",
    connection="AzureWebJobsStorage",
)
def process_powermgmt_event_check(msg: func.QueueMessage):
    try:
        logging.debug(
            "Processing deferred wait check: %s", msg.get_body().decode("utf-8")
        )
        event = DeferredWaitPowerActionCheck.model_validate_json(
            msg.get_body().decode("utf-8")
        )
    except ValidationError as e:
        logging.error("Couldn't process message: %s", str(e))
        raise

    check_powermgmt_status(event)


@app.queue_trigger(
    arg_name="msg",
    queue_name="%QUEUE_PROCESS_UPDATEMGMT_EVENT%",
    connection="AzureWebJobsStorage",
)
def process_updatemgmt_event(msg: func.QueueMessage):

    body = msg.get_json()
    logging.debug("Processing message: %s", body)

    event = func.EventGridEvent(
        id=body.get("id"),
        subject=body.get("subject"),
        data=body.get("data"),
        event_type=body.get("eventType"),
        event_time=body.get("eventTime"),
        data_version=body.get("dataVersion"),
        topic=body.get("topic"),
    )
    management_groups = [os.getenv("RESOURCEGRAPH_MG_SCOPE")]
    resourcegraph_manager = ResourceGraphManager(
        get_cached_credentials(), management_groups=management_groups
    )

    correl_id = event.get_json().get("CorrelationId")
    try:
        resources = resourcegraph_manager.get_vm_ids_for_maintenance_run(correl_id)
    except Exception as e:
        logging.exception(
            "Could not query over scopes %s vms for maintenance run: %s",
            management_groups,
            e,
        )
        raise

    is_pre_maintenance: bool = "PreMaintenance" in event.event_type
    is_post_maintenance = not is_pre_maintenance

    if not resources:
        logging.info("Could not find any resources for event %s", event.id)
        return

    vm_managers_by_sub: dict[str, any] = {}
    all_alerts_by_sub: dict[str, any] = {}

    for resource in resources:
        details = decode_resource_id(resource.properties.get("resourceId"))
        sub_id = details.get("sub_id")

        vm_manager = vm_managers_by_sub.get(sub_id) or vm_managers_by_sub.setdefault(
            sub_id, VMManager(sub_id, get_cached_credentials())
        )
        alerts = all_alerts_by_sub.get(sub_id) or all_alerts_by_sub.setdefault(
            sub_id, resourcegraph_manager.query_metric_alerts(subscription_id=sub_id)
        )

        try:
            vm_name = details.get("name")
            vm: VM = vm_manager.get(details.get("resource_group"), vm_name)
            logging.debug("Found %s", vm)
        except ResourceNotFoundError:
            logging.warning("Could not find vm %s", vm_name)
            continue

        if vm.tags.get(RESOURCE_TAGKEY_POWERMGMT_EXEMPT):
            if not vm.is_running() and is_pre_maintenance:
                logging.warning(
                    "VM %s is not running, but has opted out of powermgmt, so will miss this maintenance window. Ignoring event %s",
                    vm.id,
                    event.event_type,
                )
            continue

        if is_pre_maintenance:
            enqueue_power_action(
                enqueue_queue_name=os.getenv("QUEUE_POWERMGMT_EVENTS"),
                power_action=vm.create_power_action(
                    RESOURCE_ACTION_START_PRE_UPDATES,
                    find_matching_alert_ids(alerts, vm.id, details.get("type")),
                ),
            )

        elif (
            is_post_maintenance
            and RESOURCE_TAGVAL_STARTED_FOR_MAINTENANCE
            in vm.tags.get(RESOURCE_TAGKEY_POWERMGMT, "")
        ):
            enqueue_power_action(
                enqueue_queue_name=os.getenv("QUEUE_POWERMGMT_EVENTS"),
                power_action=vm.create_power_action(
                    RESOURCE_ACTION_STOP_POST_UPDATES,
                    find_matching_alert_ids(alerts, vm.id, details.get("type")),
                ),
            )


def enqueue_power_action(
    *, enqueue_queue_name: str, power_action: ResourcePowerAction
) -> None:

    queue_client = QueueManager(
        os.getenv("STORAGE_ACCOUNT"), get_cached_credentials(), enqueue_queue_name
    )
    logging.debug("Sending %s", power_action)
    try:
        queue_client.send(power_action.model_dump_json())
    except Exception as e:
        logging.exception(
            "Could not enqueue action power action %s: %s", power_action, e
        )
        raise


def enqueue_deferred_check_action(
    power_action_check: DeferredWaitPowerActionCheck, *, visibility_timeout=60
) -> None:
    enqueue_queue_name = os.getenv("QUEUE_POWERMGMT_DEFERRED_CHECKS")
    queue_client = QueueManager(
        os.getenv("STORAGE_ACCOUNT"), get_cached_credentials(), enqueue_queue_name
    )
    logging.debug("Sending %s", power_action_check)
    try:
        queue_client.send(
            power_action_check.model_dump_json(), visibility_timeout=visibility_timeout
        )
    except Exception as e:
        logging.exception(
            "Could not enqueue action power action check %s: %s", power_action_check, e
        )
        raise


def enqueue_sub_checks(
    *,
    enqueue_queue_name: str,
    mg_id: str = None,
    sub_id: str = None,
    last_check: str = None,
) -> List[Subscription]:

    queue_client = QueueManager(
        os.getenv("STORAGE_ACCOUNT"), get_cached_credentials(), enqueue_queue_name
    )

    mg_mgr = ManagementGroupsManager(get_cached_credentials())
    subs: List[Subscription] = []

    if mg_id:
        subs = mg_mgr.get_subs_in_mg(mg_id)
    elif sub_id and (sub := mg_mgr.get_sub(sub_id)) is not None:
        subs.append(sub)

    subs = [sub for sub in subs if sub.is_active()]

    for sub in subs:
        try:
            sub_check = SubscriptionCheck(**sub.model_dump(), last_check=last_check)
            logging.info("Sending %s", sub_check)
            queue_client.send(sub_check.model_dump_json())
        except Exception as e:
            logging.exception("Could not enqueue sub check: %s", e)
            raise

    return subs
