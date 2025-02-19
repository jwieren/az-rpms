from dataclasses import dataclass
import logging
from typing import Dict, List

from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient

from azure.mgmt.rdbms.postgresql_flexibleservers.models import (
    Server as PostgreSQLServer,
)
from azure.core.exceptions import HttpResponseError

from ..util import AzureResource, ThrottledRetryableError
from .azuremanager import AzureManager


@dataclass(kw_only=True)
class PGSQL(AzureResource):
    base_server: PostgreSQLServer

    @classmethod
    def from_server(cls, server: PostgreSQLServer) -> "PGSQL":
        return cls(
            id=server.id, base_server=server, power_state=server.state.casefold()
        )

    @property
    def tags(self) -> Dict[str, str]:
        return self.base_server.tags or {}

    @tags.setter
    def tags(self, value: Dict[str, str]) -> None:
        self.base_server.tags = value

    def to_base_server(self) -> PostgreSQLServer:
        return self.base_server

    # see https://learn.microsoft.com/en-us/rest/api/postgresql/flexibleserver/servers/get?view=rest-postgresql-flexibleserver-2022-12-01&tabs=HTTP#serverstate
    # Postgres can take a long time to power down, so only stop if it is actually in read state
    def in_stoppable_state(self):
        return self.power_state.casefold() in [
            "ready",
        ]

    # Likewise, postgres can take a long time to start, so only stop if it is actually in stopped state
    def in_startable_state(self):
        return self.power_state.casefold() in [
            "stopped",
        ]


class PostgreSQLManager(AzureManager):
    """Creates a wrapper around PostgreSQLManagementClient"""

    def __init__(self, subscription_id, credentials):
        """
        Initialize a wrapper around PostgreSQLManagementClient.

        Args:
            subscription_id (str): The Azure subscription ID.
        """
        AzureManager.__init__(self, subscription_id, credentials)
        self._client = PostgreSQLManagementClient(
            self._credential, self._subscription_id
        )
        logging.debug("Creating postgresql manager for sub %s", self._subscription_id)

    def get(self, resource_group, server_name) -> PGSQL:
        server: PostgreSQLServer = self._client.servers.get(resource_group, server_name)
        return PGSQL.from_server(
            server,
        )

    def start(
        self,
        server: PGSQL,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):
        if not continuation_token:
            logging.warning(
                "PostgreSQL Server %s is being started...%s",
                server.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of start on %s...", server.name)

        poller = self._client.servers.begin_start(
            server.resource_group, server.name, continuation_token=continuation_token
        )

        if return_poller:
            return poller.continuation_token()
        else:
            poller.result(timeout=timeout)
            # succeeded, inprogress
            return poller.status().casefold()

    def stop(
        self,
        server: PGSQL,
        *,
        reason: str = None,
        return_poller: bool = False,
        timeout: float = None,
        continuation_token: str = None,
    ):

        if not continuation_token:
            logging.warning(
                "PostgreSQL Server %s is being stopped...%s",
                server.name,
                f" ({reason})" if reason else "",
            )
        else:
            logging.info("Checking status of stop on %s...", server.name)

        try:
            poller = self._client.servers.begin_stop(
                server.resource_group,
                server.name,
                continuation_token=continuation_token,
            )

            if return_poller:
                return poller.continuation_token()
            else:
                poller.result(timeout=timeout)
                # succeeded, inprogress
                return poller.status().casefold()
        except HttpResponseError as error:
            logging.warning(
                "PostgreSQL Server %s experienced error during stop: %s",
                server.name,
                error.message,
            )
            return "failure"

    def get_tagged_resources(self, tags: List[str]) -> List[PGSQL]:
        logging.debug("Querying PostgreSQL Flexible Servers")

        try:
            servers: List[PostgreSQLServer] = self._client.servers.list()
        except HttpResponseError as e:
            if e.error.code == "ResourceCollectionRequestsThottled":
                raise ThrottledRetryableError(e) from e
            raise

        matching_servers: List[PGSQL] = []

        for server in servers:
            # Check if all target tags are in the VM's tags
            if server.tags and any(server.tags.get(key) is not None for key in tags):
                tagged_server: PGSQL = PGSQL.from_server(server)
                matching_servers.append(tagged_server)
                logging.debug("PostgresSQL Server %s discovered", server.name)
            else:
                logging.debug("PostgresSQL Server %s ignored", server.name)

        return matching_servers
