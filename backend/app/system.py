from __future__ import annotations

import platform
import importlib
from datetime import datetime, timezone
from typing import Any

import psutil

try:
    wmi = importlib.import_module("wmi")
except Exception:  # pragma: no cover - fallback when package unavailable
    wmi = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gpu_utilization_map() -> dict[str, float]:
    if wmi is None:
        return {}

    try:
        perf_wmi = wmi.WMI(namespace="root\\CIMV2")
        samples = perf_wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
    except Exception:
        return {}

    totals: dict[str, float] = {}
    for item in samples:
        name = str(getattr(item, "Name", ""))
        value = float(getattr(item, "UtilizationPercentage", 0) or 0)
        if "engtype_3d" not in name.lower():
            continue

        gpu_prefix = name.split("_engtype", 1)[0]
        totals[gpu_prefix] = min(100.0, totals.get(gpu_prefix, 0.0) + value)

    return totals


def get_system_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=None)
    gpu_list = []
    utilization_by_prefix = _gpu_utilization_map()

    if wmi is not None:
        try:
            c = wmi.WMI()
            for idx, gpu in enumerate(c.Win32_VideoController()):
                load_percentage = getattr(gpu, "CurrentRefreshRate", None)
                util = utilization_by_prefix.get(f"pid_{idx}")
                gpu_list.append(
                    {
                        "name": getattr(gpu, "Name", "Unknown GPU"),
                        "driver_version": getattr(gpu, "DriverVersion", None),
                        "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                        "current_refresh_rate": load_percentage,
                        "utilization_percent": util,
                    }
                )
        except Exception:
            gpu_list = []

    return {
        "timestamp": now_iso(),
        "cpu": {
            "percent": cpu_percent,
            "count_physical": psutil.cpu_count(logical=False),
            "count_logical": psutil.cpu_count(logical=True),
        },
        "memory": {
            "total": memory.total,
            "used": memory.used,
            "percent": memory.percent,
        },
        "gpu": gpu_list,
    }


def get_hardware_summary() -> dict[str, Any]:
    base: dict[str, Any] = {
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }

    gpu_data = []
    if wmi is None:
        gpu_data = [{"name": "Unavailable", "error": "wmi package not available"}]
    else:
        try:
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                gpu_data.append(
                    {
                        "name": getattr(gpu, "Name", "Unknown GPU"),
                        "driver_version": getattr(gpu, "DriverVersion", None),
                        "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                    }
                )
        except Exception as exc:
            gpu_data = [{"name": "Unavailable", "error": str(exc)}]

    base["gpu"] = gpu_data
    return base
