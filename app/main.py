from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

from app.csms import ApplianceChargePoint
from app.models import (
    ApplianceInfo,
    ApplianceStatus,
    RemoteStartRequest,
    RemoteStopRequest,
    LoadLevelControlRequest,
    ChargingRateUnitChangeRequest,
    SimpleChargingProfile,
    DataTransferRequest,
    GroupCreate,
    GroupUpdate,
    GroupView,
    GroupMemberAppliance,
    GroupMemberGroup,
    ApplianceLimitUpdate,
    EffectiveCapsResponse,
)
from app.state import REGISTRY
from app.groups import GROUPS
from app.dashboard import build_dashboard, effective_caps

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("home-ocpp-csms")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Home OCPP Appliance Gateway", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class StarletteWebSocketAdapter:
    """
    Adapter to satisfy the ocpp library expectation of a connection with:
      - async recv() -> str
      - async send(str)
    """
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws

    async def recv(self) -> str:
        return await self.ws.receive_text()

    async def send(self, msg: str) -> None:
        await self.ws.send_text(msg)


@app.get("/", response_class=HTMLResponse)
def ui_home():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ---------------------- Appliance APIs ----------------------

@app.get("/api/appliances", response_model=Dict[str, ApplianceStatus])
def list_appliances() -> Dict[str, ApplianceStatus]:
    return REGISTRY.list_status()


@app.get("/api/appliances/info", response_model=Dict[str, ApplianceInfo])
def list_appliance_info() -> Dict[str, ApplianceInfo]:
    return REGISTRY.list_info()


def _get_cp_or_404(cp_id: str) -> ApplianceChargePoint:
    entry = REGISTRY.get(cp_id)
    if not entry or not entry.status.connected:
        raise HTTPException(status_code=404, detail=f"{cp_id} not connected")
    return entry.charge_point


@app.patch("/api/appliances/{cp_id}/limit", response_model=ApplianceStatus)
def set_appliance_limit(cp_id: str, body: ApplianceLimitUpdate):
    entry = REGISTRY.get(cp_id)
    if not entry:
        raise HTTPException(status_code=404, detail="appliance not found")
    entry.status.individual_limit_watts = body.limit_watts
    return entry.status


@app.post("/api/appliances/{cp_id}/remote_start")
async def remote_start(cp_id: str, body: RemoteStartRequest) -> Any:
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.remote_start(id_tag=body.id_tag, connector_id=body.connector_id)


@app.post("/api/appliances/{cp_id}/remote_stop")
async def remote_stop(cp_id: str, body: RemoteStopRequest) -> Any:
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.remote_stop(transaction_id=body.transaction_id)


@app.post("/api/appliances/{cp_id}/power_cap")
async def set_power_cap(cp_id: str, body: SimpleChargingProfile) -> Any:
    if body.unit != "W":
        raise HTTPException(status_code=400, detail="Only unit=W is supported for appliance caps")
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.set_power_cap_watts(
            limit_watts=body.limit,
            connector_id=body.connector_id,
            transaction_id=body.transaction_id,
            duration_seconds=body.duration_seconds,
        )


@app.post("/api/appliances/{cp_id}/data_transfer")
async def send_data_transfer(cp_id: str, body: DataTransferRequest) -> Any:
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.data_transfer(body.vendor_id, body.message_id, body.data)


@app.post("/api/appliances/{cp_id}/load_level_control")
async def send_load_level_control(cp_id: str, body: LoadLevelControlRequest) -> Any:
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.load_level_control(
            target_load_kw=body.target_load_kw,
            command=body.command,
            duration_minutes=body.duration_minutes,
            reason=body.reason,
            transaction_id=body.transaction_id,
        )


@app.post("/api/appliances/{cp_id}/charging_rate_unit_change")
async def send_rate_unit_change(cp_id: str, body: ChargingRateUnitChangeRequest) -> Any:
    entry = REGISTRY.get(cp_id)
    cp = _get_cp_or_404(cp_id)
    async with entry.lock:
        return await cp.charging_rate_unit_change(
            desired_unit=body.desired_unit,
            reason=body.reason,
            effective_time=body.effective_time,
            transaction_id=body.transaction_id,
        )


# ---------------------- Group APIs ----------------------

def _group_to_view(g) -> GroupView:
    return GroupView(
        group_id=g.group_id,
        name=g.name,
        limit_watts=g.limit_watts,
        appliances=set(g.appliances),
        subgroups=set(g.subgroups),
    )


@app.get("/api/groups", response_model=Dict[str, GroupView])
def list_groups():
    return {gid: _group_to_view(g) for gid, g in GROUPS.list_groups().items()}


@app.post("/api/groups", response_model=GroupView)
def create_group(body: GroupCreate):
    g = GROUPS.create_group(body.group_id, body.name, body.limit_watts)
    return _group_to_view(g)


@app.patch("/api/groups/{group_id}", response_model=GroupView)
def update_group(group_id: str, body: GroupUpdate):
    g = GROUPS.get_group(group_id)
    if not g:
        raise HTTPException(status_code=404, detail="group not found")
    if body.name is not None:
        g.name = body.name
    # allow null explicitly
    g.limit_watts = body.limit_watts
    return _group_to_view(g)


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    GROUPS.delete_group(group_id)
    return JSONResponse({"ok": True})


@app.post("/api/groups/{group_id}/appliances", response_model=GroupView)
def add_appliance_to_group(group_id: str, body: GroupMemberAppliance):
    try:
        g = GROUPS.add_appliance(group_id, body.cp_id)
        return _group_to_view(g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/groups/{group_id}/appliances/{cp_id}", response_model=GroupView)
def remove_appliance_from_group(group_id: str, cp_id: str):
    try:
        g = GROUPS.remove_appliance(group_id, cp_id)
        return _group_to_view(g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/groups/{group_id}/subgroups", response_model=GroupView)
def add_subgroup(group_id: str, body: GroupMemberGroup):
    try:
        g = GROUPS.add_subgroup(group_id, body.child_group_id)
        return _group_to_view(g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/groups/{group_id}/subgroups/{child_group_id}", response_model=GroupView)
def remove_subgroup(group_id: str, child_group_id: str):
    try:
        g = GROUPS.remove_subgroup(group_id, child_group_id)
        return _group_to_view(g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------- Cap resolution + application ----------------------

@app.get("/api/caps/effective", response_model=EffectiveCapsResponse)
def api_effective_caps():
    caps, reasons = effective_caps()
    return EffectiveCapsResponse(caps=caps, reasons=reasons)


@app.post("/api/caps/apply")
async def apply_effective_caps():
    """
    Push effective caps to connected appliances using SetChargingProfile in watts.
    This assumes appliance controllers interpret SetChargingProfile as a power cap.
    """
    caps, _ = effective_caps()
    results: Dict[str, Any] = {}

    for cp_id, cap in caps.items():
        entry = REGISTRY.get(cp_id)
        if not entry or not entry.status.connected:
            results[cp_id] = {"ok": False, "error": "not connected"}
            continue
        if cap is None:
            results[cp_id] = {"ok": True, "skipped": "no cap"}
            continue
        try:
            async with entry.lock:
                res = await entry.charge_point.set_power_cap_watts(
                    limit_watts=float(cap),
                    connector_id=1,
                    transaction_id=entry.status.active_transaction_id,
                    duration_seconds=None,
                )
            results[cp_id] = {"ok": True, "response": res}
        except Exception as e:
            results[cp_id] = {"ok": False, "error": str(e)}

    return results


# ---------------------- Dashboard API ----------------------

@app.get("/api/dashboard")
def dashboard(service_limit_w: Optional[int] = None):
    """
    Returns totals, per-appliance loads, per-group loads, and recent history.
    Optionally supply service_limit_w to compute headroom.
    Example: /api/dashboard?service_limit_w=20000
    """
    return build_dashboard(service_limit_w=service_limit_w)


# ---------------------- OCPP WebSocket endpoint ----------------------

@app.websocket("/ocpp/{cp_id}")
async def ocpp_ws(websocket: WebSocket, cp_id: str):
    # Accept subprotocol if the client requests it.
    # Many OCPP clients will request "ocpp1.6".
    await websocket.accept(subprotocol="ocpp1.6")

    adapter = StarletteWebSocketAdapter(websocket)
    cp = ApplianceChargePoint(cp_id, adapter)
    REGISTRY.upsert(cp_id, cp)
    await cp.on_connect_init()

    try:
        await cp.start()
    except WebSocketDisconnect:
        log.info("Disconnected: %s", cp_id)
    except Exception as e:
        log.exception("OCPP session error for %s: %s", cp_id, e)
    finally:
        REGISTRY.mark_disconnected(cp_id)
        try:
            await websocket.close()
        except Exception:
            pass
