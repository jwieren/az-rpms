from dataclasses import dataclass
import logging
from typing import Dict, List

from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.storage.v2023_05_01.models._models_py3 import StorageAccount

from azure.core.exceptions import ResourceExistsError, HttpResponseError

from ..util import AzureResource, PowerMgmtRetryableError, ThrottledRetryableError
from .azuremanager import AzureManager


@dataclass(kw_only=True)
class StorageAccountSFTPFeature(AzureResource):
    base_storage_account: StorageAccount

    @classmethod
    def from_storage_account(cls, sa: StorageAccount) -> "StorageAccountSFTPFeature":
        return cls(
            id=sa.id,
            base_storage_account=sa,
            power_state="enabled" if sa.is_sftp_enabled else "disabled",
        )

    @property
    def tags(self) -> Dict[str, str]:
        return self.base_storage_account.tags or {}

    @tags.setter
    def tags(self, value: Dict[str, str]) -> None:
        self.base_storage_account.tags = value

    def to_base_storage_account(self) -> StorageAccount:
        return self.base_storage_account

    def is_running(self):
        return self.base_storage_account.is_sftp_enabled

    def in_stoppable_state(self):
        return self.is_running()

    def in_startable_state(self):
        return not self.is_running()


class StorageAccountSFTPManager(AzureManager):
    """Creates a wrapper around StorageManagementClient, for the purposes of enabling or disabling the sftp service"""

    def __init__(self, subscription_id, credential):
        """
        Initialize a wrapper around StorageManagementClient.

        Args:
            subscription_id (str): The Azure subscription ID.
        """
        AzureManager.__init__(self, subscription_id, credential)
        self._client = StorageManagementClient(self._credential, self._subscription_id)
        logging.debug("Creating storagemanagementclient for sub %s", subscription_id)

    def get(self, resource_group, sa_name) -> StorageAccountSFTPFeature:
        sa: StorageAccount = self._client.storage_accounts.get_properties(
            resource_group, sa_name
        )
        return StorageAccountSFTPFeature.from_storage_account(
            sa,
        )

    def start(
        self,
        sa: StorageAccountSFTPFeature,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "SA sftp service %s is being enabled...%s",
                sa.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of enable sftp on %s...", sa.name)

        sa.base_storage_account.is_sftp_enabled = True

        poller = self._client.storage_accounts.begin_create(
            sa.resource_group,
            sa.name,
            parameters=sa.base_storage_account,
            continuation_token=continuation_token,
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
        sa: StorageAccountSFTPFeature,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "SA sftp service %s is being disabled...%s",
                sa.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of disable of sftp on %s...", sa.name)

        sa.base_storage_account.is_sftp_enabled = False

        poller = self._client.storage_accounts.begin_create(
            sa.resource_group,
            sa.name,
            parameters=sa.base_storage_account,
            continuation_token=continuation_token,
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

    def get_tagged_resources(
        self, tags: List[str], *, additional_required_tags: dict = None
    ) -> List[StorageAccountSFTPFeature]:
        logging.debug("Querying StorageAccounts")

        try:
            sa_pager = self._client.storage_accounts.list()
        except HttpResponseError as e:
            if e.error.code == "ResourceCollectionRequestsThottled":
                raise ThrottledRetryableError(e) from e
            raise

        matching_sas: List[StorageAccountSFTPFeature] = []

        for sa_page in sa_pager.by_page():
            for sa in sa_page:
                # Check if all target tags are in the Storage Accounts's tags
                if (
                    sa.tags
                    and any(sa.tags.get(key) is not None for key in tags)
                    and all(
                        sa.tags.get(key) is not None
                        for key in additional_required_tags.keys()
                    )
                ):
                    tagged_sa: StorageAccountSFTPFeature = (
                        StorageAccountSFTPFeature.from_storage_account(sa)
                    )
                    matching_sas.append(tagged_sa)
                    logging.debug("sa %s discovered", sa.name)
                else:
                    logging.debug("sa %s ignored", sa.name)

        return matching_sas
