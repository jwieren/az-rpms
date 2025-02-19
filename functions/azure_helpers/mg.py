import logging
from typing import List, Optional

from azure.mgmt.managementgroups import ManagementGroupsAPI
from azure.mgmt.subscription import SubscriptionClient

from azure.core.exceptions import ResourceNotFoundError

from .util import (
    Subscription,
)


class ManagementGroupsManager:
    """Initialize a wrapper around ManagementGroupsManager."""

    def __init__(self, credentials):
        self._credentials = credentials
        self._client = ManagementGroupsAPI(self._credentials)

    def get_subs_in_mg(self, management_group_id):
        subscriptions: List[Subscription] = []

        try:
            # Get the management group and its descendants
            response = self._client.management_groups.get_descendants(
                group_id=management_group_id
            )

            for entity in response:
                logging.debug(entity)
                if "subscriptions" in entity.type:
                    if sub := self.get_sub(entity.name):
                        subscriptions.append(sub)

        except Exception as e:
            logging.exception("An error occurred: %s", e)

        return subscriptions

    def get_sub(self, sub_id: str) -> Optional[Subscription]:

        try:
            sub_details = SubscriptionClient(self._credentials).subscriptions.get(
                sub_id
            )
            return Subscription(
                id=sub_id,
                display_name=sub_details.display_name,
                state=sub_details.state,
            )
        except ResourceNotFoundError:
            return None
