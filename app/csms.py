from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from ocpp.routing import on
from ocpp.v16 import ChargePoint as ChargePointV16
from ocpp.v16 import call, call_result
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus

from app.state import REGISTRY


class ApplianceChargePoint(ChargePointV16):
    """
    OCPP 1.6J charge point session.
    Each appliance controller connects as its own charge point (cp_id).
    """

    async def on_connect_init(self) -> None:
        REGISTRY.upsert(self.id, self)

    @on(Action.boot_notification)
    async def on_boot_notification(self, charge_point_model: str, charge_point_vendor: str, **kwargs):
        cp = REGISTRY.upsert(self.id, self)
        cp.status.last_status = "BootNotification"
        cp.status.last_error = None
        cp.status.last_heartbeat_ts = time.time()
        cp.status.tags.update({"vendor": str(charge_point_vendor), "model": str(charge_point_model)})

        return call_result.BootNotificationPayload(
            current_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            interval=30,
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self):
        cp = REGISTRY.get(self.id)
        if cp:
            cp.status.last_heartbeat_ts = time.time()
        return call_result.HeartbeatPayload(current_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    @on(Action.status_notification)
    async def on_status_notification(self, connector_id: int, error_code: str, status: str, **kwargs):
        cp = REGISTRY.get(self.id)
        if cp:
            cp.status.last_status = str(status)
            cp.status.last_error = None if (not error_code or error_code == "NoError") else str(error_code)
        return call_result.StatusNotificationPayload()

    @on(Action.authorize)
    async def on_authorize(self, id_tag: str, **kwargs):
        return call_result.AuthorizePayload(id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id: int, id_tag: str, meter_start: int, timestamp: str, **kwargs):
        cp = REGISTRY.get(self.id)
        tx_id = int(time.time())
        if cp:
            cp.status.active_transaction_id = tx_id
            cp.status.last_status = "TransactionStarted"
            cp.status.last_meter.update({"meter_start": meter_start, "timestamp": timestamp})
        return call_result.StartTransactionPayload(transaction_id=tx_id, id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, transaction_id: int, meter_stop: int, timestamp: str, **kwargs):
        cp = REGISTRY.get(self.id)
        if cp:
            cp.status.last_status = "TransactionStopped"
            cp.status.last_meter.update({"meter_stop": meter_stop, "timestamp": timestamp})
            cp.status.active_transaction_id = None
        return call_result.StopTransactionPayload(id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.meter_values)
    async def on_meter_values(self, connector_id: int, meter_value: Any, **kwargs):
        cp = REGISTRY.get(self.id)
        if cp:
            cp.status.last_meter.update({"connector_id": connector_id, "meter_value": meter_value})
        return call_result.MeterValuesPayload()

    @on(Action.data_transfer)
    async def on_data_transfer(self, vendor_id: str, message_id: Optional[str] = None, data: Optional[str] = None, **kwargs):
        cp = REGISTRY.get(self.id)
        if cp:
            cp.status.last_meter.update({"data_transfer": {"vendor_id": vendor_id, "message_id": message_id, "data": data}})
        return call_result.DataTransferPayload(status="Accepted", data="OK")

    # ---------------- CSMS -> Appliance helpers ----------------

    async def remote_start(self, id_tag: str = "HOME", connector_id: int = 1) -> Dict[str, Any]:
        req = call.RemoteStartTransactionPayload(id_tag=id_tag, connector_id=connector_id)
        return await self.call(req)

    async def remote_stop(self, transaction_id: int) -> Dict[str, Any]:
        req = call.RemoteStopTransactionPayload(transaction_id=transaction_id)
        return await self.call(req)

    async def set_power_cap_watts(
        self,
        limit_watts: float,
        connector_id: int = 1,
        transaction_id: Optional[int] = None,
        duration_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Use SetChargingProfile as a generalized power cap for appliances.
        """
        charging_schedule: Dict[str, Any] = {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": float(limit_watts)}],
        }
        if duration_seconds is not None:
            charging_schedule["duration"] = int(duration_seconds)

        profile: Dict[str, Any] = {
            "chargingProfileId": int(time.time()),
            "stackLevel": 1,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": charging_schedule,
        }
        if transaction_id is not None:
            profile["transactionId"] = int(transaction_id)

        req = call.SetChargingProfilePayload(connector_id=connector_id, cs_charging_profiles=profile)
        return await self.call(req)

    async def data_transfer(self, vendor_id: str, message_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = call.DataTransferPayload(vendor_id=vendor_id, message_id=message_id, data=json.dumps(payload))
        return await self.call(req)

    async def load_level_control(
        self,
        target_load_kw: float,
        command: str = "SET",
        duration_minutes: int = 0,
        reason: str = "Home Load Management",
        transaction_id: Optional[int] = None,
        vendor_id: str = "home.appliance",
    ) -> Dict[str, Any]:
        return await self.data_transfer(
            vendor_id=vendor_id,
            message_id="LoadLevelControl",
            payload={
                "command": command,
                "targetLoadLevel": target_load_kw,
                "duration": duration_minutes,
                "reason": reason,
                "transactionId": transaction_id,
            },
        )

    async def charging_rate_unit_change(
        self,
        desired_unit: str,
        reason: str = "Home Demand Response",
        effective_time: Optional[str] = None,
        transaction_id: Optional[int] = None,
        vendor_id: str = "home.appliance",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"desiredUnit": desired_unit, "reason": reason, "transactionId": transaction_id}
        if effective_time:
            payload["effectiveTime"] = effective_time

        return await self.data_transfer(vendor_id=vendor_id, message_id="ChargingRateUnitChange", payload=payload)
