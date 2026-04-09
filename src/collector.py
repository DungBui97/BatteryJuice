"""Battery data collection from macOS system APIs."""

from __future__ import annotations

import json
import plistlib
import subprocess


def _run(cmd: list[str]) -> bytes:
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    return result.stdout


def _get_ioreg_data() -> dict:
    raw = _run(["ioreg", "-r", "-n", "AppleSmartBattery", "-a"])
    if not raw:
        return {}
    try:
        items = plistlib.loads(raw)
        return items[0] if items else {}
    except Exception:
        return {}


_MODEL_CACHE: str | None = None

def _get_model() -> str:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    try:
        raw = _run(["system_profiler", "SPHardwareDataType", "-json"])
        data = json.loads(raw)
        hw = data.get("SPHardwareDataType", [{}])[0]
        _MODEL_CACHE = hw.get("machine_model", "MacBook")
    except Exception:
        _MODEL_CACHE = "MacBook"
    return _MODEL_CACHE


def collect() -> dict:
    batt = _get_ioreg_data()
    if not batt:
        return {}

    # Temperature: Intel Macs report Kelvin*100 (e.g. 29815 = 298.15K = 25°C)
    # Apple Silicon Macs report millicelsius (e.g. 2500 = 25.0°C)
    # Heuristic: Kelvin*100 values are always > 27315 (0°C); millicelsius for
    # typical battery temps (0–60°C) are 0–6000 — no overlap.
    temp_raw = batt.get("Temperature", 0)
    if temp_raw:
        if temp_raw > 10000:
            temp_c = round((temp_raw / 100) - 273.15, 1)  # Intel: Kelvin*100
        else:
            temp_c = round(temp_raw / 100, 1)             # Apple Silicon: millicelsius
    else:
        temp_c = None

    # Power draw from PowerTelemetryData
    telemetry = batt.get("PowerTelemetryData", {})
    system_load_mw = telemetry.get("SystemLoad", 0) if isinstance(telemetry, dict) else 0
    power_draw_w = round(system_load_mw / 1000, 2) if system_load_mw else None

    design_cap = batt.get("DesignCapacity")
    max_cap = batt.get("AppleRawMaxCapacity")
    current_pct = batt.get("CurrentCapacity")
    cycle_count = batt.get("CycleCount")
    voltage_mv = batt.get("Voltage")
    is_charging = bool(batt.get("IsCharging", False))

    return {
        "cycle_count": cycle_count,
        "max_capacity_mah": max_cap,
        "design_capacity_mah": design_cap,
        "current_pct": current_pct,
        "power_draw_w": power_draw_w,
        "temperature_c": temp_c,
        "voltage_mv": voltage_mv,
        "is_charging": is_charging,
        "model": _get_model(),
    }
