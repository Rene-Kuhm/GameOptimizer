from __future__ import annotations

import ctypes
import importlib
import importlib.util
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
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

try:
    win32pdh = importlib.import_module("win32pdh")
except Exception:  # pragma: no cover - optional dependency
    win32pdh = None


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
    global_3d_utilization = 0.0
    for item in samples:
        name = str(getattr(item, "Name", ""))
        value = float(getattr(item, "UtilizationPercentage", 0) or 0)
        if "engtype_3d" not in name.lower():
            continue

        global_3d_utilization = min(100.0, global_3d_utilization + value)
        gpu_prefix = name.split("_engtype", 1)[0]
        totals[gpu_prefix] = min(100.0, totals.get(gpu_prefix, 0.0) + value)

    if global_3d_utilization > 0:
        totals["__global_engtype_3d__"] = global_3d_utilization

    return totals


def _wmi_video_controllers() -> list[Any]:
    if wmi is None:
        return []

    try:
        client = wmi.WMI()
        return list(client.Win32_VideoController())
    except Exception:
        return []


def _gpu_vendor(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    return "unknown"


def _gpu_vendor_present(vendor: str) -> bool:
    for adapter in _wmi_video_controllers():
        name = str(getattr(adapter, "Name", ""))
        if _gpu_vendor(name) == vendor:
            return True
    return False


def _has_native_amd_lib() -> bool:
    if importlib.util.find_spec("pyadl") is not None:
        return True

    for dll_name in ["atiadlxx.dll", "atiadlxy.dll"]:
        try:
            ctypes.WinDLL(dll_name)
            return True
        except Exception:
            continue

    return False


def _has_native_intel_lib() -> bool:
    for module_name in ["intel_gpu", "intel_gpu_tools"]:
        if importlib.util.find_spec(module_name) is not None:
            return True

    for dll_name in ["igdml64.dll", "igdml32.dll"]:
        try:
            ctypes.WinDLL(dll_name)
            return True
        except Exception:
            continue

    return False


def _collect_vendor_gpu_stub(vendor: str) -> dict[str, Any] | None:
    if vendor == "amd":
        return {
            "backend": "amd_native_hook",
            "available": _has_native_amd_lib(),
            "note": "AMD native provider hook active; using non-native telemetry in Phase 1.",
        }
    if vendor == "intel":
        return {
            "backend": "intel_native_hook",
            "available": _has_native_intel_lib(),
            "note": "Intel native provider hook active; using non-native telemetry in Phase 1.",
        }
    return None


def _telemetry_backend(base_backend: str, note: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"backend": base_backend}
    if note:
        payload["note"] = note
    payload.update(extra)
    return payload


@dataclass(frozen=True)
class ProviderOutput:
    items: list[dict[str, Any]]
    reason: str


class GpuTelemetryProvider(Protocol):
    source: str

    def is_available(self) -> tuple[bool, str]:
        ...

    def collect(self) -> ProviderOutput:
        ...


# Deterministic confidence scoring for GPU telemetry quality.
# - native vendor backends: high confidence
# - WMI GPUEngine correlation: medium confidence
# - static/fallback adapters only: low confidence
GPU_CONFIDENCE_BY_SOURCE: dict[str, tuple[float, str]] = {
    "nvml": (0.95, "Vendor-native NVIDIA telemetry is active."),
    "amd": (0.90, "Vendor-native AMD telemetry is active."),
    "intel": (0.90, "Vendor-native Intel telemetry is active."),
    "wmi": (0.65, "WMI GPUEngine counters are correlated with adapters."),
    "pdh": (0.40, "PDH GPU counters are available with limited detail."),
    "fallback": (0.25, "Static adapter info is available, without live utilization."),
    "unavailable": (0.05, "No GPU telemetry backend is currently available."),
}


def _confidence_for_source(source: str) -> tuple[float, str]:
    score, reason = GPU_CONFIDENCE_BY_SOURCE.get(source, GPU_CONFIDENCE_BY_SOURCE["unavailable"])
    return score, reason


class NvidiaNvmlProvider:
    source = "nvml"

    def is_available(self) -> tuple[bool, str]:
        if pynvml is None:
            return False, "pynvml package unavailable"

        try:
            pynvml.nvmlInit()
            count = int(pynvml.nvmlDeviceGetCount())
            if count <= 0:
                return False, "NVML initialized but no devices"
            return True, "NVML initialized"
        except Exception as exc:
            return False, f"NVML init failed: {exc}"
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

    def collect(self) -> ProviderOutput:
        if pynvml is None:
            return ProviderOutput([], "pynvml package unavailable")

        try:
            pynvml.nvmlInit()
        except Exception as exc:
            return ProviderOutput([], f"NVML init failed: {exc}")

        items: list[dict[str, Any]] = []
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            driver_version = pynvml.nvmlSystemGetDriverVersion()
            driver = (
                driver_version.decode("utf-8", errors="ignore")
                if isinstance(driver_version, bytes)
                else str(driver_version)
            )

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
                        "telemetry_backend": _telemetry_backend(
                            "nvml",
                            vendor_native=True,
                            driver=driver,
                        ),
                    }
                )
            return ProviderOutput(items, "NVML sampling succeeded")
        except Exception as exc:
            return ProviderOutput([], f"NVML collection failed: {exc}")
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass


class AmdNativeHookProvider:
    source = "amd"

    def is_available(self) -> tuple[bool, str]:
        if not _gpu_vendor_present("amd"):
            return False, "AMD adapter not detected"

        if not _has_native_amd_lib():
            return False, "AMD native library not found"

        return True, "AMD adapter and native library detected"

    def collect(self) -> ProviderOutput:
        # Phase 1 hook: explicit strategy exists, with graceful no-data behavior.
        return ProviderOutput([], "AMD native telemetry hook detected but not yet implemented")


class IntelNativeHookProvider:
    source = "intel"

    def is_available(self) -> tuple[bool, str]:
        if not _gpu_vendor_present("intel"):
            return False, "Intel adapter not detected"

        if not _has_native_intel_lib():
            return False, "Intel native library not found"

        return True, "Intel adapter and native library detected"

    def collect(self) -> ProviderOutput:
        # Phase 1 hook: explicit strategy exists, with graceful no-data behavior.
        return ProviderOutput([], "Intel native telemetry hook detected but not yet implemented")


class WmiGpuEngineProvider:
    source = "wmi"

    def is_available(self) -> tuple[bool, str]:
        if wmi is None:
            return False, "wmi package unavailable"

        try:
            perf_wmi = wmi.WMI(namespace="root\\CIMV2")
            perf_wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
            return True, "WMI GPUEngine class available"
        except Exception as exc:
            return False, f"WMI GPUEngine unavailable: {exc}"

    def collect(self) -> ProviderOutput:
        utilization_by_prefix = _gpu_utilization_map()
        if not utilization_by_prefix:
            return ProviderOutput([], "No WMI GPUEngine utilization samples")

        items: list[dict[str, Any]] = []
        try:
            for idx, gpu in enumerate(_wmi_video_controllers()):
                name = getattr(gpu, "Name", "Unknown GPU")
                utilization_percent = utilization_by_prefix.get(f"pid_{idx}")
                if utilization_percent is None:
                    pid_prefix = f"pid_{idx}_"
                    prefixed_values = [
                        value for key, value in utilization_by_prefix.items() if key.startswith(pid_prefix)
                    ]
                    if prefixed_values:
                        utilization_percent = max(prefixed_values)

                if utilization_percent is None:
                    utilization_percent = utilization_by_prefix.get("__global_engtype_3d__")

                vendor = _gpu_vendor(str(name))
                payload = {
                    "name": name,
                    "driver_version": getattr(gpu, "DriverVersion", None),
                    "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                    "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                    "utilization_percent": utilization_percent,
                    "vendor": vendor,
                    "telemetry_backend": _telemetry_backend(
                        "wmi_gpue",
                        namespace="root\\CIMV2",
                        counter="Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine",
                    ),
                }
                stub = _collect_vendor_gpu_stub(vendor)
                if stub:
                    payload["telemetry_backend"]["native_hook"] = stub
                items.append(payload)
        except Exception as exc:
            return ProviderOutput([], f"WMI correlation failed: {exc}")

        return ProviderOutput(items, "WMI GPUEngine utilization correlated to adapters")


class PdhFallbackProvider:
    source = "pdh"

    def is_available(self) -> tuple[bool, str]:
        if win32pdh is None:
            return False, "win32pdh unavailable"

        try:
            instances, counters = win32pdh.EnumObjectItems(
                None,
                None,
                "GPU Engine",
                win32pdh.PERF_DETAIL_WIZARD,
                0,
            )
            if not instances or "Utilization Percentage" not in counters:
                return False, "PDH GPU Engine counters not available"
            return True, "PDH GPU Engine counters available"
        except Exception as exc:
            return False, f"PDH probe failed: {exc}"

    def collect(self) -> ProviderOutput:
        items: list[dict[str, Any]] = []
        for gpu in _wmi_video_controllers():
            name = getattr(gpu, "Name", "Unknown GPU")
            vendor = _gpu_vendor(str(name))
            payload = {
                "name": name,
                "driver_version": getattr(gpu, "DriverVersion", None),
                "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                "utilization_percent": None,
                "vendor": vendor,
                "telemetry_backend": _telemetry_backend(
                    "pdh_gpu_engine",
                    note="PDH fallback active in probe mode; live sampling not enabled in Phase 1.",
                ),
            }
            stub = _collect_vendor_gpu_stub(vendor)
            if stub:
                payload["telemetry_backend"]["native_hook"] = stub
            items.append(payload)

        return ProviderOutput(items, "PDH probe fallback returned adapter metadata")


class StaticVideoControllerFallbackProvider:
    source = "fallback"

    def is_available(self) -> tuple[bool, str]:
        if wmi is None:
            return False, "wmi package unavailable"
        return True, "WMI adapter summary available"

    def collect(self) -> ProviderOutput:
        items: list[dict[str, Any]] = []
        try:
            for gpu in _wmi_video_controllers():
                name = getattr(gpu, "Name", "Unknown GPU")
                vendor = _gpu_vendor(str(name))
                payload = {
                    "name": name,
                    "driver_version": getattr(gpu, "DriverVersion", None),
                    "adapter_ram": int(getattr(gpu, "AdapterRAM", 0) or 0),
                    "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                    "utilization_percent": None,
                    "vendor": vendor,
                    "telemetry_backend": _telemetry_backend(
                        "wmi_video_controller",
                        note="Fallback adapter metadata only.",
                    ),
                }
                stub = _collect_vendor_gpu_stub(vendor)
                if stub:
                    payload["telemetry_backend"]["native_hook"] = stub
                items.append(payload)
        except Exception as exc:
            return ProviderOutput([], f"Static fallback failed: {exc}")

        return ProviderOutput(items, "Fallback adapter metadata collected")


def _provider_chain() -> list[GpuTelemetryProvider]:
    return [
        NvidiaNvmlProvider(),
        AmdNativeHookProvider(),
        IntelNativeHookProvider(),
        WmiGpuEngineProvider(),
        PdhFallbackProvider(),
        StaticVideoControllerFallbackProvider(),
    ]


def _select_gpu_telemetry() -> tuple[str, float, str, list[dict[str, Any]]]:
    notes: list[str] = []

    for provider in _provider_chain():
        is_available, availability_note = provider.is_available()
        if not is_available:
            notes.append(f"{provider.source}: {availability_note}")
            continue

        result = provider.collect()
        if result.items:
            confidence, confidence_reason = _confidence_for_source(provider.source)
            reason = f"{confidence_reason} {result.reason}"
            return provider.source, confidence, reason, result.items

        notes.append(f"{provider.source}: {result.reason}")

    confidence, confidence_reason = _confidence_for_source("unavailable")
    reason = confidence_reason
    if notes:
        reason = f"{confidence_reason} {' | '.join(notes[:2])}"
    return "unavailable", confidence, reason, []


def get_system_metrics() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=None)
    gpu_source, gpu_confidence, gpu_confidence_reason, gpu_list = _select_gpu_telemetry()

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
        "gpu_confidence": gpu_confidence,
        "gpu_confidence_reason": gpu_confidence_reason,
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
