from __future__ import annotations

from typing import Any, Dict, Optional


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def parse_ocpp16_meter_values(last_meter: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract a usable snapshot from the last stored MeterValues payload.
    Attempts to read:
      - instant_w from Power.Active.Import (W or kW)
      - energy_kwh from Energy.Active.Import.Register (Wh or kWh)
      - timestamp from latest meter entry

    Many devices vary in exact field casing; this parser is intentionally tolerant.
    """
    mv = last_meter.get("meter_value")
    if not mv or not isinstance(mv, list):
        return {"instant_w": None, "energy_kwh": None, "timestamp": None}

    best_ts = None
    instant_w = None
    energy_kwh = None

    def consider_ts(ts: Optional[str]) -> None:
        nonlocal best_ts
        if not ts:
            return
        if best_ts is None or ts > best_ts:
            best_ts = ts

    for entry in mv:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp") or entry.get("timeStamp")
        consider_ts(ts)

        sampled = entry.get("sampledValue") or entry.get("sampled_value") or []
        if not isinstance(sampled, list):
            continue

        for sv in sampled:
            if not isinstance(sv, dict):
                continue

            meas = (sv.get("measurand") or "").lower()
            unit = (sv.get("unit") or "").lower()
            value = _safe_float(sv.get("value"))
            if value is None:
                continue

            # Instantaneous power
            if "power.active.import" in meas or meas == "power.active.import":
                if unit == "kw":
                    value *= 1000.0
                instant_w = value if instant_w is None else instant_w
                continue

            if (meas == "power" or "power.active" in meas) and instant_w is None:
                if unit == "kw":
                    value *= 1000.0
                instant_w = value
                continue

            # Cumulative energy
            if "energy.active.import.register" in meas or meas == "energy.active.import.register":
                if unit == "wh":
                    energy_kwh = value / 1000.0
                elif unit == "kwh":
                    energy_kwh = value
                else:
                    energy_kwh = (value / 1000.0) if value > 1000 else value
                continue

    return {"instant_w": instant_w, "energy_kwh": energy_kwh, "timestamp": best_ts}
