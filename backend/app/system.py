from __future__ import annotations

import importlib
import platform
from datetime import datetime, timezone
from typing import Any

import psutil

try:
    wmi = importlib.import_module("wmi")
except Exception:  # pragma: no cover - fallback when package unavailable
    wmi = None

try:
    pynvml = importlib.import_module("pynvml")
except Exception:  # pragma: no cover - optional dependency
    pynvml = None


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


def _gpu_vendor(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    return "unknown"


def _collect_nvml_gpu_metrics() -> list[dict[str, Any]]:
    if pynvml is None:
        return []

    try:
        pynvml.nvmlInit()
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    try:
        device_count = pynvml.nvmlDeviceGetCount()
        driver_version = pynvml.nvmlSystemGetDriverVersion()
        driver = driver_version.decode("utf-8", errors="ignore") if isinstance(driver_version, bytes) else str(driver_version)

        for index in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)

            gpu_name = name.decode("utf-8", errors="ignore") if isinstance(name, bytes) else str(name)
            items.append(
                {
                    "name": gpu_name,
                    "driver_version": driver,
                    "adapter_ram": int(mem.total),
                    "current_refresh_rate": None,
                    "utilization_percent": float(util.gpu),
                    "memory_used": int(mem.used),
                    "memory_total": int(mem.total),
                    "vendor": "nvidia",
                }
            )
    except Exception:
        return []
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass

    return items


def _collect_wmi_gpu_metrics() -> list[dict[str, Any]]:
    if wmi is None:
        return []

    utilization_by_prefix = _gpu_utilization_map()
    items: list[dict[str, Any]] = []
    try:
        client = wmi.WMI()
        for idx, gpu in enumerate(client.Win32_VideoController()):
            name = getattr(gpu, "Name", "Unknown GPU")
            items.append(
                {
                    "name": name,
                    "driver_version": getattr(gpu, "DriverVersion", None),
                    "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                    "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                    "utilization_percent": utilization_by_prefix.get(f"pid_{idx}"),
                    "vendor": _gpu_vendor(str(name)),
                }
            )
    except Exception:
        return []

    return items


def _collect_vendor_gpu_stub(vendor: str) -> dict[str, Any] | None:
    if vendor == "amd":
        return {
            "backend": "amd_stub",
            "available": False,
            "note": "Direct AMD telemetry backend not configured; using WMI/fallback.",
        }
    if vendor == "intel":
        return {
            "backend": "intel_stub",
            "available": False,
            "note": "Direct Intel telemetry backend not configured; using WMI/fallback.",
        }
    return None


def _collect_fallback_gpu_summary() -> list[dict[str, Any]]:
    if wmi is None:
        return []

    items: list[dict[str, Any]] = []
    try:
        client = wmi.WMI()
        for gpu in client.Win32_VideoController():
            name = getattr(gpu, "Name", "Unknown GPU")
            vendor = _gpu_vendor(str(name))
            payload = {
                "name": name,
                "driver_version": getattr(gpu, "DriverVersion", None),
                "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                "utilization_percent": None,
                "vendor": vendor,
            }
            stub = _collect_vendor_gpu_stub(vendor)
            if stub:
                payload["telemetry_backend"] = stub
            items.append(payload)
    except Exception:
        return []

    return items


def get_system_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=None)
    gpu_source = "fallback"

    gpu_list = _collect_nvml_gpu_metrics()
    if gpu_list:
        gpu_source = "nvml"
    else:
        gpu_list = _collect_wmi_gpu_metrics()
        if gpu_list:
            gpu_source = "wmi"
        else:
            gpu_list = _collect_fallback_gpu_summary()

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
        "gpu_source": gpu_source,
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
