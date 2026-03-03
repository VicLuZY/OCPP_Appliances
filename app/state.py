from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.models import ApplianceInfo, ApplianceStatus


@dataclass
class ConnectedCP:
    cp_id: str
    charge_point: Any
    info: ApplianceInfo = field(default_factory=lambda: ApplianceInfo(cp_id=""))
    status: ApplianceStatus = field(default_factory=lambda: ApplianceStatus(cp_id="", connected=True))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Registry:
    """
    In-memory registry. Swap to SQLite/Redis for persistence.
    """
    def __init__(self) -> None:
        self._cps: Dict[str, ConnectedCP] = {}

    def upsert(self, cp_id: str, charge_point: Any) -> ConnectedCP:
        if cp_id not in self._cps:
            info = ApplianceInfo(cp_id=cp_id)
            status = ApplianceStatus(cp_id=cp_id, connected=True, last_heartbeat_ts=time.time())
            self._cps[cp_id] = ConnectedCP(cp_id=cp_id, charge_point=charge_point, info=info, status=status)
        else:
            self._cps[cp_id].charge_point = charge_point
            self._cps[cp_id].status.connected = True
            self._cps[cp_id].status.last_heartbeat_ts = time.time()
        return self._cps[cp_id]

    def mark_disconnected(self, cp_id: str) -> None:
        cp = self._cps.get(cp_id)
        if cp:
            cp.status.connected = False

    def get(self, cp_id: str) -> Optional[ConnectedCP]:
        return self._cps.get(cp_id)

    def list_status(self) -> Dict[str, ApplianceStatus]:
        return {cp_id: cp.status for cp_id, cp in self._cps.items()}

    def list_info(self) -> Dict[str, ApplianceInfo]:
        return {cp_id: cp.info for cp_id, cp in self._cps.items()}


REGISTRY = Registry()
