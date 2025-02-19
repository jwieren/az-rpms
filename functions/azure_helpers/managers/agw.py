from dataclasses import dataclass
import logging
from typing import Dict, List

from azure.mgmt.network.models import ApplicationGateway
from azure.mgmt.network import NetworkManagementClient

from azure.core.exceptions import ResourceExistsError, HttpResponseError
from ..util import AzureResource, PowerMgmtRetryableError, ThrottledRetryableError
from .azuremanager import AzureManager


@dataclass(kw_only=True)
class AppGW(AzureResource):
    base_agw: ApplicationGateway

    @classmethod
    def from_agw(cls, agw: ApplicationGateway) -> "AppGW":
        return cls(id=agw.id, base_agw=agw, power_state=agw.operational_state)

    @property
    def tags(self) -> Dict[str, str]:
        return self.base_agw.tags or {}

    @tags.setter
    def tags(self, value: Dict[str, str]) -> None:
        self.base_agw.tags = value

    def to_base_agw(self) -> ApplicationGateway:
        return self.base_agw

    def is_running(self):
        return self.power_state.casefold() in ["running", "starting"]

    def in_stoppable_state(self):
        return self.is_running()

    def in_startable_state(self):
        return not self.is_running()


class AppGatewayManager(AzureManager):
    """Creates a wrapper around ComputeManagementClient"""

    def __init__(self, subscription_id, credentials):
        """
        Initialize a wrapper around NetworkManagementClient.

        Args:
            subscription_id (str): The Azure subscription ID.
        """
        AzureManager.__init__(self, subscription_id, credentials)
        self._client = NetworkManagementClient(self._credential, self._subscription_id)
        logging.debug("Creating networkmanager for sub %s", subscription_id)

    def get(self, resource_group, agw_name) -> AppGW:
        agw: ApplicationGateway = self._client.application_gateways.get(
            resource_group, agw_name
        )
        return AppGW.from_agw(
            agw,
        )

    def start(
        self,
        agw: AppGW,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "AGW %s is being started...%s",
                agw.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of start on %s...", agw.name)

        poller = self._client.application_gateways.begin_start(
            agw.resource_group, agw.name, continuation_token=continuation_token
        )
        try:
            if return_poller:
                return poller.continuation_token()
            else:
                poller.result(timeout=timeout)
                # succeeded, inprogress
                return poller.status().casefold()
        except ResourceExistsError as e:
            # Typically another operation is in progress
            logging.warning(e.message)
            if e.error.code == "AnotherOperationInProgress":
                raise PowerMgmtRetryableError(e) from e
            raise

    def stop(
        self,
        agw: AppGW,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "AGW %s is being stopped...%s",
                agw.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of stop on %s...", agw.name)

        poller = self._client.application_gateways.begin_stop(
            agw.resource_group, agw.name, continuation_token=continuation_token
        )
        try:
            if return_poller:
                return poller.continuation_token()
            else:
                poller.result(timeout=timeout)
                # succeeded, inprogress
                return poller.status().casefold()
        except ResourceExistsError as e:
            # Typically another operation is in progress
            logging.warning(e.message)
            if e.error.code == "AnotherOperationInProgress":
                raise PowerMgmtRetryableError(e) from e
            raise

    def get_tagged_resources(self, tags: List[str]) -> List[AppGW]:
        logging.debug("Querying AppGateways")

        try:
            agws_pager = self._client.application_gateways.list_all()
        except HttpResponseError as e:
            if e.error.code == "ResourceCollectionRequestsThottled":
                raise ThrottledRetryableError(e) from e
            raise
        matching_agws: List[AppGW] = []

        for agw_page in agws_pager.by_page():
            for agw in agw_page:
                # Check if all target tags are in the VM's tags
                if agw.tags and any(agw.tags.get(key) is not None for key in tags):
                    tagged_agw: AppGW = AppGW.from_agw(agw)
                    matching_agws.append(tagged_agw)
                    logging.debug("agw %s discovered", agw.name)
                else:
                    logging.debug("agw %s ignored", agw.name)

        return matching_agws
