from dataclasses import dataclass, field
import re
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class PowerMgmtRetryableError(Exception):
    pass


class ThrottledRetryableError(Exception):
    pass


def get_power_state(instance_view):
    return next(
        (
            status.code.casefold().replace("powerstate/", "")
            for status in instance_view.statuses
            if status.code.casefold().startswith("powerstate")
        ),
        None,
    )


def get_resource_group(resource_id: str) -> str:
    return resource_id.split("/")[4]


def get_sub_id(resource_id: str) -> str:
    return resource_id.split("/")[2]


def get_name(resource_id: str) -> str:
    return resource_id.split("/")[-1]


def get_resource_type(resource_id: str) -> str:
    match = re.search(r"/providers/(.+?)/[^/]+$", resource_id)
    if match:
        return match.group(1).casefold()
    return None


@dataclass(kw_only=True)
class AzureResource:
    id: str
    power_state: str
    _power_state: str = field(init=False, repr=False)

    @property
    def sub_id(self) -> str:
        return get_sub_id(self.id)

    @property
    def resource_group(self) -> str:
        return get_resource_group(self.id)

    @property
    def name(self) -> str:
        return get_name(self.id)

    @property
    def resource_type(self) -> str:
        return get_resource_type(self.id)

    @property
    def power_state(self) -> int:
        return self._power_state

    @power_state.setter
    def power_state(self, power_state) -> None:
        self._power_state = power_state.casefold() if power_state else "unknown"

    def create_power_action(
        self,
        action: str,
        alert_ids: List[str] = None,
        sub_type: str = None,
        attempt_num: int = 1,
    ):
        if alert_ids is None:
            alert_ids = []

        return ResourcePowerAction(
            id=self.id,
            sub_type=sub_type,
            action=action,
            alert_ids=alert_ids,
            attempt_num=attempt_num,
        )


class ResourcePowerAction(BaseModel):
    id: str
    sub_type: Optional[str] = None
    action: str
    created_at: datetime = Field(default_factory=datetime.now)
    alert_ids: Optional[List[str]] = []
    attempt_num: int

    @property
    def sub_id(self) -> str:
        return get_sub_id(self.id)

    @property
    def resource_group(self) -> str:
        return get_resource_group(self.id)

    @property
    def name(self) -> str:
        return get_name(self.id)

    @property
    def resource_type(self) -> str:
        return get_resource_type(self.id)


class DeferredWaitPowerActionCheck(ResourcePowerAction):
    tag_text: Optional[str]
    continuation_token: str
    wait_retries: int

    class Config:
        extra = "allow"


class ResourceGraphItem(BaseModel):
    id: str
    name: str
    type: str
    location: str
    properties: Dict[str, Any]
    tags: Optional[Dict[str, str]] = None

    class Config:
        extra = "allow"


class Subscription(BaseModel):
    id: str
    display_name: str
    state: str

    def is_active(self):
        return self.state in ["Active", "Enabled"]
