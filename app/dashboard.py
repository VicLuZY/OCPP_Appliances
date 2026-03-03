from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.state import REGISTRY
from app.groups import GROUPS
from app.metering import parse_ocpp16_meter_values


@dataclass
class DashboardHistory:
    max_points: int = 300
    points: List[Dict[str, Any]] = field(default_factory=list)

    def push(self, t: float, total_w: float) -> None:
        self.points.append({"t": t, "total_w": total_w})
        if len(self.points) > self.max_points:
            self.points = self.points[-self.max_points:]


DASH_HISTORY = DashboardHistory()


def effective_caps() -> Tuple[Dict[str, Optional[int]], Dict[str, Dict[str, Any]]]:
    statuses = REGISTRY.list_status()
    caps: Dict[str, Optional[int]] = {}
    reasons: Dict[str, Dict[str, Any]] = {}

    for cp_id, st in statuses.items():
        candidate_caps: List[Tuple[str, int]] = []
        if st.individual_limit_watts is not None:
            candidate_caps.append(("individual", int(st.individual_limit_watts)))

        for gid in sorted(GROUPS.ancestors_of_appliance(cp_id)):
            g = GROUPS.get_group(gid)
            if g and g.limit_watts is not None:
                candidate_caps.append((f"group:{gid}", int(g.limit_watts)))

        caps[cp_id] = min((v for _, v in candidate_caps), default=None)
        reasons[cp_id] = {"candidates": [{"source": s, "watts": v} for s, v in candidate_caps]}

    return caps, reasons


def _group_descendants(group_id: str) -> Set[str]:
    visited: Set[str] = set()
    stack = [group_id]
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        g = GROUPS.get_group(cur)
        if not g:
            continue
        stack.extend(list(g.subgroups))
    visited.discard(group_id)
    return visited


def _group_all_appliances(group_id: str) -> Set[str]:
    g = GROUPS.get_group(group_id)
    if not g:
        return set()
    aps = set(g.appliances)
    for desc in _group_descendants(group_id):
        dg = GROUPS.get_group(desc)
        if dg:
            aps |= set(dg.appliances)
    return aps


def build_dashboard(service_limit_w: Optional[int] = None) -> Dict[str, Any]:
    statuses = REGISTRY.list_status()
    caps, _ = effective_caps()

    appliances: Dict[str, Any] = {}
    total_w = 0.0
    connected = 0

    for cp_id, st in statuses.items():
        parsed = parse_ocpp16_meter_values(st.last_meter)
        w = parsed["instant_w"] or 0.0

        if st.connected:
            connected += 1
            total_w += float(w)

        appliances[cp_id] = {
            "cp_id": cp_id,
            "connected": st.connected,
            "last_heartbeat_ts": st.last_heartbeat_ts,
            "last_status": st.last_status,
            "last_error": st.last_error,
            "instant_w": parsed["instant_w"],
            "energy_kwh": parsed["energy_kwh"],
            "meter_ts": parsed["timestamp"],
            "individual_limit_watts": st.individual_limit_watts,
            "effective_limit_watts": caps.get(cp_id),
            "direct_groups": sorted(list(GROUPS.appliance_groups(cp_id))),
        }

    groups: Dict[str, Any] = {}
    for gid, g in GROUPS.list_groups().items():
        member_aps = _group_all_appliances(gid)
        group_w = 0.0
        for cp_id in member_aps:
            ap = appliances.get(cp_id)
            if ap and ap["connected"] and ap["instant_w"] is not None:
                group_w += float(ap["instant_w"])
        groups[gid] = {
            "group_id": gid,
            "name": g.name,
            "limit_watts": g.limit_watts,
            "instant_w_sum": group_w,
            "appliances_total": len(member_aps),
            "appliances_connected": sum(1 for cp in member_aps if appliances.get(cp, {}).get("connected")),
            "direct_appliances": sorted(list(g.appliances)),
            "direct_subgroups": sorted(list(g.subgroups)),
        }

    headroom_w = None
    if service_limit_w is not None:
        headroom_w = float(service_limit_w) - float(total_w)

    DASH_HISTORY.push(time.time(), float(total_w))

    return {
        "totals": {
            "total_instant_w": total_w,
            "connected_appliances": connected,
            "known_appliances": len(statuses),
            "service_limit_w": service_limit_w,
            "headroom_w": headroom_w,
        },
        "appliances": appliances,
        "groups": groups,
        "history": DASH_HISTORY.points,
    }
