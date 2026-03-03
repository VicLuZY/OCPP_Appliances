from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Set


class ApplianceKind(str, Enum):
    EVSE = "evse"
    WATER_HEATER = "water_heater"
    DRYER = "dryer"
    RANGE = "range"
    OVEN = "oven"
    HEAT_PUMP = "heat_pump"
    AIR_CONDITIONER = "air_conditioner"
    OTHER_240V = "other_240v"


class ApplianceInfo(BaseModel):
    cp_id: str
    kind: ApplianceKind = ApplianceKind.OTHER_240V
    description: Optional[str] = None
    rated_watts: Optional[int] = None
    connectors: int = 1


class ApplianceStatus(BaseModel):
    cp_id: str
    connected: bool
    last_heartbeat_ts: Optional[float] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_meter: Dict[str, Any] = Field(default_factory=dict)
    active_transaction_id: Optional[int] = None
    tags: Dict[str, str] = Field(default_factory=dict)

    # Home gateway policy knobs
    individual_limit_watts: Optional[int] = None


class RemoteStartRequest(BaseModel):
    id_tag: str = "HOME"
    connector_id: int = 1


class RemoteStopRequest(BaseModel):
    transaction_id: int


class LoadLevelControlRequest(BaseModel):
    # Sent as DataTransfer vendor extension
    command: str = Field(default="SET", description="SET|INCREASE|DECREASE")
    target_load_kw: float = 0.0
    duration_minutes: int = 0
    reason: str = "Home Load Management"
    transaction_id: Optional[int] = None


class ChargingRateUnitChangeRequest(BaseModel):
    # Sent as DataTransfer vendor extension
    desired_unit: str = Field(default="A", description="A|kW")
    reason: str = "Home Demand Response"
    effective_time: Optional[str] = None
    transaction_id: Optional[int] = None


class SimpleChargingProfile(BaseModel):
    """
    Minimal profile for home caps using SetChargingProfile (OCPP 1.6).
    We use W as the canonical unit for appliance caps.
    """
    connector_id: int = 1
    transaction_id: Optional[int] = None
    unit: str = "W"     # "W" only for now
    limit: float = 0.0  # watts
    duration_seconds: Optional[int] = None


class DataTransferRequest(BaseModel):
    vendor_id: str
    message_id: str
    data: Dict[str, Any]


class GroupCreate(BaseModel):
    group_id: str
    name: str
    limit_watts: Optional[int] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    limit_watts: Optional[int] = None


class GroupView(BaseModel):
    group_id: str
    name: str
    limit_watts: Optional[int] = None
    appliances: Set[str] = Field(default_factory=set)
    subgroups: Set[str] = Field(default_factory=set)


class GroupMemberAppliance(BaseModel):
    cp_id: str


class GroupMemberGroup(BaseModel):
    child_group_id: str


class ApplianceLimitUpdate(BaseModel):
    limit_watts: Optional[int] = None


class EffectiveCapsResponse(BaseModel):
    caps: Dict[str, Optional[int]]
    reasons: Dict[str, Dict[str, Any]]
