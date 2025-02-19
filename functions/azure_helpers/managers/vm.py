from dataclasses import dataclass
import logging
from typing import Dict, List, Optional

from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.compute.models import VirtualMachine

from azure.core.exceptions import HttpResponseError

from ..util import (
    AzureResource,
    ThrottledRetryableError,
    get_power_state,
    get_resource_group,
)
from .azuremanager import AzureManager


@dataclass(kw_only=True)
class VM(AzureResource):
    base_vm: VirtualMachine

    @classmethod
    def from_vm(
        cls, vm: VirtualMachine, power_state: Optional[str] = "unknown"
    ) -> "VM":
        return cls(id=vm.id, base_vm=vm, power_state=power_state)

    @property
    def tags(self) -> Dict[str, str]:
        return self.base_vm.tags or {}

    @tags.setter
    def tags(self, value: Dict[str, str]) -> None:
        self.base_vm.tags = value

    def to_base_vm(self) -> VirtualMachine:
        return self.base_vm

    def is_running(self):
        return self.power_state.casefold() in ["running", "starting"]

    def in_stoppable_state(self):
        return self.is_running()

    def in_startable_state(self):
        return not self.is_running()


class VMManager(AzureManager):
    """Creates a wrapper around ComputeManagementClient"""

    def __init__(self, subscription_id, credentials):
        """
        Initialize a wrapper around ComputeManagementClient.

        Args:
            subscription_id (str): The Azure subscription ID.
        """
        AzureManager.__init__(self, subscription_id, credentials)
        self._client = ComputeManagementClient(credentials, self._subscription_id)
        logging.debug("Creating vmmanager for sub %s", subscription_id)

    def get(self, resource_group, vm_name) -> VM:
        vm: VirtualMachine = self._client.virtual_machines.get(resource_group, vm_name)
        return VM.from_vm(
            vm,
            get_power_state(
                self._client.virtual_machines.instance_view(
                    get_resource_group(vm.id), vm.name
                )
            ),
        )

    def start(
        self,
        vm: VM,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "VM %s is being started...%s",
                vm.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of start on %s...", vm.name)

        poller = self._client.virtual_machines.begin_start(
            vm.resource_group, vm.name, continuation_token=continuation_token
        )

        if return_poller:
            return poller.continuation_token()
        else:
            poller.result(timeout=timeout)
            # succeeded, inprogress
            result_str = poller.status().casefold()
            logging.info(
                "Polling status of start on %s returned state %s", vm.name, result_str
            )

            # There are some VMs for which the poller status remains inprogress, but the
            # machine has obviously started. If this occurs, simply return succeeded
            if result_str == "inprogress" and vm.is_running():
                logging.info("Overriding status of start on %s to succeeded", vm.name)
                result_str = "succeeded"
            return result_str

    def stop(
        self,
        vm: VM,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "VM %s is being deallocated...%s",
                vm.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of deallocation on %s...", vm.name)

        poller = self._client.virtual_machines.begin_deallocate(
            vm.resource_group, vm.name, continuation_token=continuation_token
        )

        if return_poller:
            return poller.continuation_token()
        else:
            result = poller.result(timeout=timeout)
            # succeeded, inprogress
            return poller.status().casefold()

    def get_tagged_resources(self, tags: List[str]) -> List[VM]:
        logging.debug("Querying VMs")

        try:
            vms_pager = self._client.virtual_machines.list_all()
        except HttpResponseError as e:
            if e.error.code == "ResourceCollectionRequestsThottled":
                raise ThrottledRetryableError(e) from e
            raise
        matching_vms: List[VM] = []

        for vm_page in vms_pager.by_page():
            for vm in vm_page:
                # Check if all target tags are in the VM's tags
                if vm.tags and any(vm.tags.get(key) is not None for key in tags):
                    rg = get_resource_group(vm.id)
                    power_state = get_power_state(
                        self._client.virtual_machines.instance_view(rg, vm.name)
                    )
                    tagged_vm: VM = VM.from_vm(vm, power_state)
                    matching_vms.append(tagged_vm)
                    logging.debug("vm %s discovered", vm.name)
                else:
                    logging.debug("vm %s ignored", vm.name)

        return matching_vms
