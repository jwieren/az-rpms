import time
from datetime import datetime, timedelta
import logging
from typing import List, Optional, Type

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest

from azure.core.exceptions import HttpResponseError

from .util import (
    ResourceGraphItem,
)


class ResourceGraphManager:
    """Initialize a wrapper around ResourceGraphClient"""

    def __init__(
        self,
        credentials,
        *,
        subscriptions: Optional[List[str]] = None,
        management_groups: Optional[List[str]] = None,
    ):
        self._client = ResourceGraphClient(credentials)
        self._subscriptions = subscriptions
        self._management_groups = management_groups

    def query(
        self,
        query,
        *,
        max_results: int = 1000,
    ) -> List[Type[ResourceGraphItem]]:

        def parse_wait_time(wait_time):
            # Parse the wait time from 'hh:mm:ss' format to seconds
            t = datetime.strptime(wait_time, "%H:%M:%S")
            return timedelta(
                hours=t.hour, minutes=t.minute, seconds=t.second
            ).total_seconds()

        skip_token = None
        results: List[ResourceGraphItem] = []

        while len(results) < max_results:
            request = QueryRequest(
                query=query,
                subscriptions=self._subscriptions,
                management_groups=self._management_groups,
                options={
                    "resultFormat": "objectArray",
                    "$top": min(max_results - len(results), 1000),
                    "$skip": skip_token,
                },
            )

            while True:
                try:
                    response = self._client.resources(request)
                    break
                except HttpResponseError as e:
                    if e.status_code == 429:  # Rate limiting error
                        wait_time = e.response.headers.get(
                            "x-ms-user-quota-resets-after"
                        )
                        if wait_time:
                            wait_seconds = parse_wait_time(wait_time)
                            logging.warning(
                                "Resource graph rate limit reached. Resets after %s. Waiting for %s seconds.",
                                wait_time,
                                wait_seconds,
                            )
                            time.sleep(wait_seconds)
                        else:
                            logging.warning(
                                "Resource graph rate limit reached, but no wait time provided. Waiting for 60 seconds."
                            )
                            time.sleep(10)
                    else:
                        raise  # Re-raise if it's not a rate limiting error

            for item in response.data:
                model = ResourceGraphItem.model_validate(item)
                results.append(model)

            skip_token = response.skip_token
            if not skip_token or len(results) >= max_results:
                break

        return results

    def get_vm_ids_for_maintenance_run(self, correl_id: str) -> List[ResourceGraphItem]:

        query = f"""
         maintenanceresources  
            | where type =~ 'microsoft.maintenance/applyupdates' 
            | where properties.correlationId =~ "{correl_id}"
            | where id has '/providers/microsoft.compute/virtualmachines/' 
            | order by id asc

    """
        return self.query(query)

    def query_metric_alerts(
        self,
        *,
        subscription_id=None,
        target_resource_types=[
            "microsoft.compute/virtualmachines",
            "microsoft.network/applicationgateways",
            "microsoft.dbforpostgresql/flexibleservers",
        ],
    ) -> List[ResourceGraphItem]:

        if subscription_id:
            query = f"""
            Resources 
                | where subscriptionId == '{subscription_id}' and            
                 ( (type =~ 'Microsoft.Insights/metricAlerts' and properties.targetResourceType =~ '{",".join(target_resource_types)}') 
                    or (type =~ 'microsoft.insights/activitylogalerts') )
        """
        else:
            query = f"""
            Resources 
                | where (type =~ 'Microsoft.Insights/metricAlerts' and properties.targetResourceType =~ '{",".join(target_resource_types)}') 
                    or (type =~ 'microsoft.insights/activitylogalerts')
        """
        return self.query(query)
