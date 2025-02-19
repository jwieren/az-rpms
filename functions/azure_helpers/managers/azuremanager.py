import logging
from azure.mgmt.resource import ResourceManagementClient

from azure.core.exceptions import HttpResponseError

from ..util import AzureResource


class AzureManager:
    """Base class for other wrapper classes around resource clients"""

    def __init__(self, subscription_id, credential):
        self._subscription_id = subscription_id

        self._credential = credential
        self._resourceclient = ResourceManagementClient(
            self._credential, subscription_id
        )

    def add_tags(self, resource: AzureResource, tags: dict) -> bool | None:
        """
        Merge tags to a resource
        """
        try:
            resource.tags = (
                self._resourceclient.tags.begin_update_at_scope(
                    scope=resource.id,
                    parameters={
                        "operation": "Merge",
                        "properties": {"tags": resource.tags | tags},
                    },
                )
                .result()
                .properties.tags
            )
            return True
        except HttpResponseError as error:
            logging.warning(
                "Could not update tags on %s: %s", resource.name, error.message
            )
            return None
