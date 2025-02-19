import logging

from azure.mgmt.alertsmanagement import AlertsManagementClient

from azure.mgmt.alertsmanagement.models import (
    AlertProcessingRule,
    AlertProcessingRuleProperties,
    RemoveAllActionGroups,
)


class AlertsManager:
    """Creates a wrapper around AlertsManagementClient"""

    def __init__(self, subscription_id, credential):
        self._subscription_id = subscription_id
        self._client = AlertsManagementClient(credential, self._subscription_id)

    def update_alert_processing_rule(
        self,
        target_id: str,
        rule_name: str,
        resource_group: str,
        enabled: bool = True,
    ) -> None:

        alert_processing_rule = AlertProcessingRule(
            location="global",
            properties=AlertProcessingRuleProperties(
                scopes=[target_id],
                description="This processing rule suppresses notifications to all action groups",
                conditions=None,
                actions=[RemoveAllActionGroups()],
                enabled=enabled,
            ),
        )

        self._client.alert_processing_rules.create_or_update(
            resource_group_name=resource_group,
            alert_processing_rule_name=rule_name,
            alert_processing_rule=alert_processing_rule,
        )

        logging.warning("Alert rule %s set to enabled=%s", rule_name, enabled)
