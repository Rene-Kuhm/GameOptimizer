from __future__ import annotations

import ctypes
import importlib
import importlib.util
import os
import platform
import re
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def _decode_cstr(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif hasattr(value, "raw"):
        raw = bytes(value.raw)
    else:
        return str(value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def _normalize_gpu_name(name: str) -> str:
    lowered = name.lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _coalesce_memory_usage(dedicated: int | None, shared: int | None) -> int | None:
    values = [value for value in [dedicated, shared] if value is not None and value >= 0]
    if not values:
        return None
    return int(sum(values))


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


def _gpu_util_for_adapter_index(adapter_index: int, utilization_by_prefix: dict[str, float]) -> float | None:
    utilization_percent = utilization_by_prefix.get(f"pid_{adapter_index}")
    if utilization_percent is None:
        pid_prefix = f"pid_{adapter_index}_"
        prefixed_values = [
            value for key, value in utilization_by_prefix.items() if key.startswith(pid_prefix)
        ]
        if prefixed_values:
            utilization_percent = max(prefixed_values)

    if utilization_percent is None:
        utilization_percent = utilization_by_prefix.get("__global_engtype_3d__")

    return utilization_percent


def _wmi_video_controllers() -> list[Any]:
    if wmi is None:
        return []

    try:
        client = wmi.WMI()
        return list(client.Win32_VideoController())
    except Exception:
        return []


def _wmi_video_controller_rows() -> list[tuple[int, Any]]:
    return list(enumerate(_wmi_video_controllers()))


def _gpu_adapter_memory_usage_map() -> dict[str, dict[str, int | None]]:
    if wmi is None:
        return {}

    try:
        perf_wmi = wmi.WMI(namespace="root\\CIMV2")
        rows = perf_wmi.Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory()
    except Exception:
        return {}

    memory_map: dict[str, dict[str, int | None]] = {}
    for row in rows:
        name = str(getattr(row, "Name", "") or "")
        if not name:
            continue
        dedicated = _safe_int(getattr(row, "DedicatedUsage", None), default=-1)
        shared = _safe_int(getattr(row, "SharedUsage", None), default=-1)
        memory_map[_normalize_gpu_name(name)] = {
            "memory_dedicated": dedicated if dedicated >= 0 else None,
            "memory_shared": shared if shared >= 0 else None,
            "memory_used": _coalesce_memory_usage(
                dedicated if dedicated >= 0 else None,
                shared if shared >= 0 else None,
            ),
        }

    return memory_map


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


def _load_amd_adl_library() -> tuple[Any | None, str]:
    for dll_name in ["atiadlxx.dll", "atiadlxy.dll"]:
        try:
            return ctypes.WinDLL(dll_name), dll_name
        except Exception:
            continue
    return None, ""


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


def _intel_native_module_name() -> str | None:
    for module_name in ["intel_gpu", "intel_gpu_tools"]:
        if importlib.util.find_spec(module_name) is not None:
            return module_name
    return None


def _collect_vendor_gpu_stub(vendor: str) -> dict[str, Any] | None:
    if vendor == "amd":
        adl_dll, dll_name = _load_amd_adl_library()
        has_pyadl = importlib.util.find_spec("pyadl") is not None
        return {
            "backend": "amd_native_hook",
            "available": has_pyadl or adl_dll is not None,
            "paths": {
                "pyadl": has_pyadl,
                "adl_ctypes": bool(adl_dll),
            },
            "note": (
                "AMD native Phase 2 path not active on current source; "
                f"pyadl={'yes' if has_pyadl else 'no'}, adl_dll={dll_name or 'not found'}."
            ),
        }
    if vendor == "intel":
        native_module = _intel_native_module_name()
        return {
            "backend": "intel_native_hook",
            "available": bool(native_module) or _has_native_intel_lib(),
            "paths": {
                "python_module": native_module or "not found",
                "dll_probe": _has_native_intel_lib(),
            },
            "note": (
                "Intel native Phase 2 path not active on current source; "
                f"module={native_module or 'not found'}."
            ),
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
    "intel_counter": (
        0.55,
        "Intel telemetry is correlated from Windows counters and is not vendor-native.",
    ),
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


def _record_get(data: Any, *keys: str) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data[key]
        return None

    for key in keys:
        if hasattr(data, key):
            return getattr(data, key)
    return None


def _coerce_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return list(value)
        except Exception:
            return []
    return [value]


def _extract_vendor_items_from_records(
    records: list[Any],
    *,
    vendor: str,
    backend: str,
    native_path: str,
    note: str,
) -> list[dict[str, Any]]:
    memory_usage_by_name = _gpu_adapter_memory_usage_map()
    items: list[dict[str, Any]] = []

    for record in records:
        name = _record_get(record, "name", "Name", "adapter_name", "AdapterName", "gpu_name")
        if not name:
            continue

        gpu_name = str(name)
        if _gpu_vendor(gpu_name) != vendor:
            continue

        adapter_ram = _safe_int(_record_get(record, "memory_total", "MemoryTotal", "adapter_ram"), default=0)
        memory_used = _safe_int(_record_get(record, "memory_used", "MemoryUsed"), default=-1)
        utilization_percent = _safe_float(
            _record_get(record, "utilization_percent", "Utilization", "activity_percent", "gpu_utilization")
        )

        memory_row = memory_usage_by_name.get(_normalize_gpu_name(gpu_name), {})
        if memory_used < 0:
            memory_used = _safe_int(memory_row.get("memory_used"), default=-1)
        if adapter_ram <= 0:
            adapter_ram = _safe_int(_record_get(record, "adapter_ram", "AdapterRAM"), default=0)

        items.append(
            {
                "name": gpu_name,
                "driver_version": _record_get(record, "driver_version", "DriverVersion"),
                "adapter_ram": adapter_ram,
                "current_refresh_rate": _record_get(
                    record,
                    "current_refresh_rate",
                    "CurrentRefreshRate",
                ),
                "utilization_percent": utilization_percent,
                "memory_used": memory_used if memory_used >= 0 else None,
                "memory_total": adapter_ram if adapter_ram > 0 else None,
                "vendor": vendor,
                "telemetry_backend": _telemetry_backend(
                    backend,
                    note=note,
                    vendor_native=True,
                    native_path=native_path,
                ),
            }
        )

    return items


class _ADLAdapterInfo(ctypes.Structure):
    _fields_ = [
        ("iSize", ctypes.c_int),
        ("iAdapterIndex", ctypes.c_int),
        ("strUDID", ctypes.c_char * 256),
        ("iBusNumber", ctypes.c_int),
        ("iDeviceNumber", ctypes.c_int),
        ("iFunctionNumber", ctypes.c_int),
        ("iVendorID", ctypes.c_int),
        ("strAdapterName", ctypes.c_char * 256),
        ("strDisplayName", ctypes.c_char * 256),
        ("iPresent", ctypes.c_int),
        ("iExist", ctypes.c_int),
        ("strDriverPath", ctypes.c_char * 256),
        ("strDriverPathExt", ctypes.c_char * 256),
        ("strPNPString", ctypes.c_char * 256),
        ("iOSDisplayIndex", ctypes.c_int),
    ]


class _ADLPMActivity(ctypes.Structure):
    _fields_ = [
        ("iSize", ctypes.c_int),
        ("iEngineClock", ctypes.c_int),
        ("iMemoryClock", ctypes.c_int),
        ("iVddc", ctypes.c_int),
        ("iActivityPercent", ctypes.c_int),
        ("iCurrentPerformanceLevel", ctypes.c_int),
        ("iCurrentBusSpeed", ctypes.c_int),
        ("iCurrentBusLanes", ctypes.c_int),
        ("iMaximumBusLanes", ctypes.c_int),
        ("iReserved", ctypes.c_int),
    ]


_ADL_ALLOCATIONS: list[Any] = []
_ADL_MAIN_MALLOC_CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int)


def _adl_malloc(size: int) -> int:
    buf = ctypes.create_string_buffer(max(1, size))
    _ADL_ALLOCATIONS.append(buf)
    return ctypes.addressof(buf)


_ADL_MALLOC_CB = _ADL_MAIN_MALLOC_CALLBACK(_adl_malloc)


def _collect_amd_with_pyadl() -> ProviderOutput:
    if importlib.util.find_spec("pyadl") is None:
        return ProviderOutput([], "pyadl package unavailable")

    try:
        pyadl = importlib.import_module("pyadl")
    except Exception as exc:
        return ProviderOutput([], f"pyadl import failed: {exc}")

    call_order = ["get_gpus", "get_adapters", "adapters", "devices"]
    for name in call_order:
        api = getattr(pyadl, name, None)
        if not callable(api):
            continue

        try:
            records = _coerce_records(api())
        except TypeError:
            continue
        except Exception as exc:
            return ProviderOutput([], f"pyadl {name}() failed: {exc}")

        items = _extract_vendor_items_from_records(
            records,
            vendor="amd",
            backend="pyadl",
            native_path="pyadl",
            note="AMD native telemetry via pyadl.",
        )
        if items:
            return ProviderOutput(items, f"pyadl {name}() sampling succeeded")

    return ProviderOutput([], "pyadl import succeeded but no supported telemetry API returned AMD data")


def _collect_amd_with_adl_ctypes() -> ProviderOutput:
    adl, dll_name = _load_amd_adl_library()
    if adl is None:
        return ProviderOutput([], "ADL DLL not found (atiadlxx/atiadlxy)")

    main_create = getattr(adl, "ADL_Main_Control_Create", None)
    main_destroy = getattr(adl, "ADL_Main_Control_Destroy", None)
    get_count = getattr(adl, "ADL_Adapter_NumberOfAdapters_Get", None)
    get_info = getattr(adl, "ADL_Adapter_AdapterInfo_Get", None)
    get_activity = getattr(adl, "ADL_Overdrive5_CurrentActivity_Get", None)
    get_active = getattr(adl, "ADL_Adapter_Active_Get", None)

    required = [main_create, main_destroy, get_count, get_info, get_activity]
    if any(symbol is None for symbol in required):
        return ProviderOutput([], f"ADL functions missing in {dll_name}")

    assert main_create is not None
    assert main_destroy is not None
    assert get_count is not None
    assert get_info is not None
    assert get_activity is not None

    main_create_fn: Any = main_create
    main_destroy_fn: Any = main_destroy
    get_count_fn: Any = get_count
    get_info_fn: Any = get_info
    get_activity_fn: Any = get_activity
    get_active_fn: Any = get_active

    main_create_fn.argtypes = [_ADL_MAIN_MALLOC_CALLBACK, ctypes.c_int]
    main_create_fn.restype = ctypes.c_int
    main_destroy_fn.argtypes = []
    main_destroy_fn.restype = ctypes.c_int
    get_count_fn.argtypes = [ctypes.POINTER(ctypes.c_int)]
    get_count_fn.restype = ctypes.c_int
    get_info_fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_info_fn.restype = ctypes.c_int
    get_activity_fn.argtypes = [ctypes.c_int, ctypes.POINTER(_ADLPMActivity)]
    get_activity_fn.restype = ctypes.c_int
    if get_active_fn is not None:
        get_active_fn.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        get_active_fn.restype = ctypes.c_int

    create_rc = main_create_fn(_ADL_MALLOC_CB, 1)
    if create_rc != 0:
        return ProviderOutput([], f"ADL init failed rc={create_rc}")

    memory_usage_by_name = _gpu_adapter_memory_usage_map()
    wmi_driver_by_name: dict[str, str | None] = {}
    wmi_ram_by_name: dict[str, int] = {}
    for _, controller in _wmi_video_controller_rows():
        name = str(getattr(controller, "Name", "") or "")
        if _gpu_vendor(name) != "amd":
            continue
        key = _normalize_gpu_name(name)
        wmi_driver_by_name[key] = getattr(controller, "DriverVersion", None)
        wmi_ram_by_name[key] = _safe_int(getattr(controller, "AdapterRAM", 0) or 0)

    try:
        count = ctypes.c_int()
        count_rc = get_count_fn(ctypes.byref(count))
        if count_rc != 0 or count.value <= 0:
            return ProviderOutput([], f"ADL adapter count failed rc={count_rc}")

        adapter_array = (_ADLAdapterInfo * count.value)()
        info_rc = get_info_fn(ctypes.cast(adapter_array, ctypes.c_void_p), ctypes.sizeof(adapter_array))
        if info_rc != 0:
            return ProviderOutput([], f"ADL adapter info failed rc={info_rc}")

        items: list[dict[str, Any]] = []
        for adapter in adapter_array:
            name = _decode_cstr(adapter.strAdapterName) or _decode_cstr(adapter.strDisplayName)
            if not name or _gpu_vendor(name) != "amd":
                continue

            if get_active_fn is not None:
                active = ctypes.c_int()
                active_rc = get_active_fn(adapter.iAdapterIndex, ctypes.byref(active))
                if active_rc == 0 and active.value == 0:
                    continue

            activity = _ADLPMActivity()
            activity.iSize = ctypes.sizeof(_ADLPMActivity)
            activity_rc = get_activity_fn(adapter.iAdapterIndex, ctypes.byref(activity))
            if activity_rc != 0:
                continue

            key = _normalize_gpu_name(name)
            memory_row = memory_usage_by_name.get(key, {})
            adapter_ram = wmi_ram_by_name.get(key, 0)
            memory_used = _safe_int(memory_row.get("memory_used"), default=-1)
            items.append(
                {
                    "name": name,
                    "driver_version": wmi_driver_by_name.get(key),
                    "adapter_ram": adapter_ram,
                    "current_refresh_rate": None,
                    "utilization_percent": float(activity.iActivityPercent),
                    "memory_used": memory_used if memory_used >= 0 else None,
                    "memory_total": adapter_ram if adapter_ram > 0 else None,
                    "vendor": "amd",
                    "telemetry_backend": _telemetry_backend(
                        "adl_overdrive5",
                        note="AMD native telemetry via ADL Overdrive5; memory usage from WMI GPUAdapterMemory when available.",
                        vendor_native=True,
                        native_path="ctypes_adl",
                        library=dll_name,
                    ),
                }
            )

        if not items:
            return ProviderOutput([], "ADL initialized but no AMD activity samples were returned")

        return ProviderOutput(items, f"ADL telemetry sampling succeeded via {dll_name}")
    except Exception as exc:
        return ProviderOutput([], f"ADL collection failed: {exc}")
    finally:
        try:
            main_destroy_fn()
        except Exception:
            pass


class AmdNativeHookProvider:
    source = "amd"

    def is_available(self) -> tuple[bool, str]:
        if not _gpu_vendor_present("amd"):
            return False, "AMD adapter not detected"

        has_pyadl = importlib.util.find_spec("pyadl") is not None
        _, dll_name = _load_amd_adl_library()
        if not has_pyadl and not dll_name:
            return False, "AMD native path unavailable: pyadl missing and ADL DLL not found"

        return True, f"AMD native candidates detected (pyadl={'yes' if has_pyadl else 'no'}, adl={dll_name or 'none'})"

    def collect(self) -> ProviderOutput:
        pyadl_result = _collect_amd_with_pyadl()
        if pyadl_result.items:
            return pyadl_result

        adl_result = _collect_amd_with_adl_ctypes()
        if adl_result.items:
            return adl_result

        return ProviderOutput(
            [],
            f"AMD native telemetry unavailable after probes: pyadl=({pyadl_result.reason}); adl=({adl_result.reason})",
        )


def _collect_intel_with_python_module() -> ProviderOutput:
    module_name = _intel_native_module_name()
    if module_name is None:
        return ProviderOutput([], "Intel native python module unavailable")

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return ProviderOutput([], f"Intel native module import failed ({module_name}): {exc}")

    call_order = ["get_gpus", "get_adapters", "adapters", "devices"]
    for name in call_order:
        api = getattr(module, name, None)
        if not callable(api):
            continue

        try:
            records = _coerce_records(api())
        except TypeError:
            continue
        except Exception as exc:
            return ProviderOutput([], f"{module_name} {name}() failed: {exc}")

        items = _extract_vendor_items_from_records(
            records,
            vendor="intel",
            backend="intel_python_native",
            native_path=module_name,
            note=f"Intel native telemetry via {module_name}.",
        )
        if items:
            return ProviderOutput(items, f"{module_name} {name}() sampling succeeded")

    return ProviderOutput([], f"{module_name} loaded but no supported telemetry API returned Intel data")


class IntelNativeHookProvider:
    source = "intel"

    def is_available(self) -> tuple[bool, str]:
        if not _gpu_vendor_present("intel"):
            return False, "Intel adapter not detected"

        module_name = _intel_native_module_name()
        if module_name is None:
            return False, "Intel native python module not found"

        return True, f"Intel native module detected: {module_name}"

    def collect(self) -> ProviderOutput:
        return _collect_intel_with_python_module()


class IntelCounterCorrelationProvider:
    source = "intel_counter"

    def is_available(self) -> tuple[bool, str]:
        if not _gpu_vendor_present("intel"):
            return False, "Intel adapter not detected"

        if wmi is None:
            return False, "wmi package unavailable"

        utilization = _gpu_utilization_map()
        if not utilization:
            return False, "WMI GPUEngine counters unavailable for Intel correlation"

        return True, "Intel adapter detected with WMI GPUEngine counters"

    def collect(self) -> ProviderOutput:
        utilization_by_prefix = _gpu_utilization_map()
        memory_usage_by_name = _gpu_adapter_memory_usage_map()

        items: list[dict[str, Any]] = []
        for idx, gpu in _wmi_video_controller_rows():
            name = str(getattr(gpu, "Name", "Unknown GPU"))
            if _gpu_vendor(name) != "intel":
                continue

            normalized = _normalize_gpu_name(name)
            memory_row = memory_usage_by_name.get(normalized, {})
            adapter_ram = _safe_int(getattr(gpu, "AdapterRAM", 0) or 0)
            memory_used = _safe_int(memory_row.get("memory_used"), default=-1)
            utilization_percent = _gpu_util_for_adapter_index(idx, utilization_by_prefix)
            items.append(
                {
                    "name": name,
                    "driver_version": getattr(gpu, "DriverVersion", None),
                    "adapter_ram": adapter_ram,
                    "current_refresh_rate": getattr(gpu, "CurrentRefreshRate", None),
                    "utilization_percent": utilization_percent,
                    "memory_used": memory_used if memory_used >= 0 else None,
                    "memory_total": adapter_ram if adapter_ram > 0 else None,
                    "vendor": "intel",
                    "telemetry_backend": _telemetry_backend(
                        "intel_wmi_counter",
                        note="Intel fallback telemetry correlated from WMI GPUEngine/GPUAdapterMemory counters.",
                        vendor_native=False,
                        native_path="wmi_counter_correlation",
                    ),
                }
            )

        if not items:
            return ProviderOutput([], "No Intel adapters were sampled during WMI counter correlation")

        return ProviderOutput(items, "Intel telemetry correlated from WMI counters (non-native)")


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
    chain: list[GpuTelemetryProvider] = [
        NvidiaNvmlProvider(),
        AmdNativeHookProvider(),
        IntelNativeHookProvider(),
        IntelCounterCorrelationProvider(),
        WmiGpuEngineProvider(),
        PdhFallbackProvider(),
        StaticVideoControllerFallbackProvider(),
    ]

    preferred = os.environ.get("GAME_OPTIMIZER_TELEMETRY_PROVIDER", "").strip().lower()
    if not preferred:
        return chain

    preferred_idx = next((idx for idx, provider in enumerate(chain) if provider.source == preferred), None)
    if preferred_idx is None:
        return chain

    preferred_provider = chain.pop(preferred_idx)
    chain.insert(0, preferred_provider)
    return chain


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
        reason = f"{confidence_reason} {' | '.join(notes[:3])}"
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
