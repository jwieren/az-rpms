"""Generic helper data types and functions used by the power management function app"""

import json
import re
from typing import List, Optional
import logging
from datetime import datetime, timezone
import pytz
from croniter import croniter
from pydantic import BaseModel
import holidays

from azure_helpers.util import Subscription, ResourceGraphItem, AzureResource

UTC = timezone.utc


class ConditionEvalCriteria(BaseModel):
    resource_group: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None


class UnparsableCondition(Exception):
    pass


class SubscriptionCheck(Subscription):
    last_check: Optional[str] = None


def decode_resource_id(resource_id: str):
    """Given an azure resource id, return a map containing decoded parts of the name

    Args:
        resource_id (str): A list of discovered alerts

    Returns:
        a map containing the sub_id, resource_group, type, name
    """
    split = resource_id.split("/")
    return {
        "sub_id": split[2],
        "resource_group": split[4],
        "type": "/".join(split[6:7]),
        "name": split[8],
    }


def find_matching_alert_ids(
    all_alerts: List[ResourceGraphItem],
    resource_id: str,
    resource_type: str = "microsoft.compute/virtualmachines",
) -> List[str]:
    """Given a list of alerts, find all alerts that would apply to a given resource_id and type

    Args:
        all_alerts (List[MetricAlert]): A list of discovered alerts
        resource_id (str): The id of a resource we want to test exists
        resource_type (str): The type of resource

    Returns:
        List[str]: A list of alert_ids that would be triggered by this resource
    """

    def scope_matches(alert: ResourceGraphItem):
        return any(
            [
                resource_id.casefold().startswith(scope)
                for scope in alert.properties.get("scopes", [])
            ]
        )

    def condition_matches(alert: ResourceGraphItem):
        try:
            if (expr := alert.properties.get("condition", None)) is not None:
                return evaluate_condition(
                    expr,
                    ConditionEvalCriteria(
                        resource_id=resource_id,
                        resource_group=resource_id.split("/")[4],
                        resource_type=resource_type,
                    ),
                )
            return True
        except UnparsableCondition as e:
            logging.debug("Could not parse alert id %s: %s", alert.id, e)
            return False

    return [
        alert.id
        for alert in all_alerts
        if scope_matches(alert) and condition_matches(alert)
    ]


def evaluate_condition(expr: any, criteria: ConditionEvalCriteria) -> bool:
    """This function takes the condition of an alert rule, and determines if a resource applies to it.

    Args:
        expr (any): This will be of the form of a condition block within an alert rule
        criteria (ConditionEvalCriteria): An object describing the resource we are testing
            the condition against

    Returns:
        bool: False if the condition contains a field within ConditionEvalCriteria that doesn't match,
              otherwise True

    Raises:
        ValueError: If the expression is invalid or cannot be evaluated.
    """

    def eval_dict(expr: any, criteria: ConditionEvalCriteria):
        field = expr.get("field")
        equals = expr.get("equals")
        result = False

        if len(expr.keys()) != 2 or not field or not equals:
            raise UnparsableCondition(expr)

        equals = equals.casefold()

        if field == "resourceGroup":
            result = (
                criteria.resource_group.casefold() == equals
                if criteria.resource_group
                else False
            )
        elif field == "resourceId":
            result = (
                criteria.resource_id.casefold() == equals
                if criteria.resource_id
                else False
            )
        elif field == "resourceType":
            result = (
                criteria.resource_type.casefold() == equals
                if criteria.resource_type
                else False
            )
        return result

    if isinstance(expr, bool):
        return expr

    if "allOf" in expr:
        return all(evaluate_condition(sub_expr, criteria) for sub_expr in expr["allOf"])

    if "anyOf" in expr:
        return any(evaluate_condition(sub_expr, criteria) for sub_expr in expr["anyOf"])

    if isinstance(expr, dict):
        return eval_dict(expr, criteria)

    raise UnparsableCondition(expr)


def nzt_now_str():
    return datetime.now(pytz.timezone("Pacific/Auckland")).strftime(
        "%Y-%m-%d %H:%M:%S NZT"
    )


TIME_SCHEDULE_PATTERN = r"^(([\-\*]|0?[0-9]|1[0-9]|2[0-3])(\:([0-5][05]))?[,/]){6}(([\-\*]|0?[0-9]|1[0-9]|2[0-3])(\:([0-5][05]))?)$"


def is_valid_powertag_value(value):
    return is_valid_cron_value(value) or is_valid_schedule_value(value)


def is_valid_cron_value(value: str) -> bool:
    return croniter.is_valid(value)


def is_valid_schedule_value(value: str) -> bool:
    return re.match(TIME_SCHEDULE_PATTERN, value)


def convert_time_schedule_to_cron(
    resource, schedule_string, event_desc, *, timezone_str="Pacific/Auckland"
):
    match = re.match(TIME_SCHEDULE_PATTERN, schedule_string)
    if not match:
        return None

    # Split the string into 7 parts
    days = schedule_string.split(",")
    # Get the current day of the week in the local timezone (0 = Monday, 6 = Sunday)
    utc_now = datetime.now(UTC)
    current_day = utc_now.astimezone(pytz.timezone(timezone_str)).weekday()
    # Extract the time for the current day
    day_time = days[current_day]

    if day_time == "-" or day_time == "*":
        logging.info(
            "time2cron %s [%s=%s]: no action for day %s",
            resource,
            event_desc,
            schedule_string,
            current_day,
        )
        return None  # No schedule for today

    parts = day_time.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0

    # Create cron expression
    return f"{minute} {hour} * * {(current_day + 1) % 7}"


def should_process_cron_event(
    last_invocation_utc,
    resource: AzureResource,
    cron_event,
    event_desc,
    cron_event_override=None,
    event_override_desc=None,
    timezone_str="Pacific/Auckland",
    correlation_id=None,
):
    now_zoned = datetime.now(UTC)
    target_timezone = pytz.timezone(timezone_str)
    now_local = now_zoned.astimezone(target_timezone)

    # If this is a subsequent triggered check, only action cron events that have transitioned since the
    # last run. IF this is the first triggered check (or we don't know when this last ran), don't factor this in
    # Also, round the last_invocation time down to the nearest minute, as sometimes there is lag in the triggering
    # of the function
    last_invocation_zoned = (
        (datetime.fromisoformat(last_invocation_utc))
        .replace(second=0, microsecond=0)
        .astimezone(UTC)
        if last_invocation_utc
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    cron_event_iter = croniter(cron_event, now_local)
    prev_cron_event_local = cron_event_iter.get_prev(datetime)

    # Convert the previous run time to UTC for comparison
    prev_cron_event_zoned = prev_cron_event_local.astimezone(UTC)
    can_action_event = last_invocation_zoned <= prev_cron_event_zoned <= now_zoned

    # Deallocate if the previous power_off cron iteration occured before now(), and there hasn't also been a subsequent power_on
    debug_str = (
        f"Actioning {event_desc} for scheduled time {prev_cron_event_zoned.astimezone(target_timezone)}"
        if can_action_event
        else "Not actioning"
    )
    if can_action_event and cron_event_override:
        cron_event_exception_iter = croniter(cron_event_override, now_local)
        prev_cron_event_exception = cron_event_exception_iter.get_prev(datetime)
        prev_cron_event_exception_zoned = prev_cron_event_exception.astimezone(UTC)
        if not (prev_cron_event_exception_zoned <= prev_cron_event_zoned <= now_zoned):
            debug_str = f"Next {event_desc} event ({prev_cron_event_exception_zoned.astimezone(target_timezone)}) overrides this {event_override_desc} ({prev_cron_event_zoned.astimezone(target_timezone)})"
            can_action_event = False
    elif prev_cron_event_zoned > now_zoned:
        debug_str = f"Current {event_desc} event ({prev_cron_event_zoned.astimezone(target_timezone)}) is after now ({now_zoned.astimezone(target_timezone)})"
    elif prev_cron_event_zoned < last_invocation_zoned:
        debug_str = f"Most recent {event_desc} event ({prev_cron_event_zoned.astimezone(target_timezone)}) was handled prior to last iteration ({last_invocation_zoned.astimezone(target_timezone)})"

    log_resource_event(
        resource,
        "croncheck",
        result="succeeded",
        **{
            (event_desc): cron_event,
            (event_override_desc): cron_event_override,
            "debug": debug_str,
            "actioning": can_action_event,
            "invocationId": correlation_id,
        },
    )

    return can_action_event


def is_public_holiday(
    check_date=None, country="NZ", timezone="Pacific/Auckland"
) -> bool | str:
    """
    Check if a given date is a public holiday.

    Parameters:
    check_date (date/datetime, optional): Date to check. Defaults to today if None.
    country (str, optional): Country code to check holidays for. Defaults to 'US'.

    Returns:
    tuple: (bool, str) - (is_holiday, holiday_name)
            is_holiday: True if date is a holiday, False otherwise
            holiday_name: Name of holiday if is_holiday is True, empty string otherwise
    """
    try:
        # If no date provided, use today
        if check_date is None:
            check_date = datetime.now(pytz.timezone("Pacific/Auckland")).date()
        # Convert datetime to date if necessary
        elif isinstance(check_date, datetime):
            check_date = check_date.date()

        # Initialise holidays for the specified country
        country_holidays = holidays.country_holidays(country)

        # Check if date is a holiday
        if check_date in country_holidays:
            return True, country_holidays.get(check_date)
        return False, ""

    except (ValueError, KeyError) as e:
        raise ValueError(f"Error checking holiday: {str(e)}") from e


def log_resource_event(resource: AzureResource, event_name, *, result=None, **kwargs):
    """log_resource_event dumps a standard json format so we can show relevant info on an Azure workspace

    Note! Be careful changing this output, as an azure workbook depends on it

    Args:
        resource (AzureResource): Any azure resource
        event_name (str): An identifying event name to log
        result (str): succeeded, inprogress, failed
        kwargs: other arguments to dump to the log
    """
    message_args = {
        "event": event_name,
        "resourceId": resource.id,
        "resourceName": resource.name,
        "powerState": resource.power_state,
        "type": resource.resource_type,
        "result": result,
    } | kwargs

    logging.info(json.dumps(message_args))
