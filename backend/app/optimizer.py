from __future__ import annotations

from typing import Any

import psutil
import win32api
import win32con
import win32process


PRIORITY_MAP = {
    "normal": win32process.NORMAL_PRIORITY_CLASS,
    "high": win32process.HIGH_PRIORITY_CLASS,
    "realtime": win32process.REALTIME_PRIORITY_CLASS,
    "below_normal": win32process.BELOW_NORMAL_PRIORITY_CLASS,
}

SAFE_BACKGROUND_PROCESSES = [
    "chrome.exe",
    "msedge.exe",
    "discord.exe",
    "spotify.exe",
    "onedrive.exe",
]


def _set_process_priority(pid: int, priority_class: int) -> tuple[bool, str | None]:
    access = win32con.PROCESS_SET_INFORMATION | win32con.PROCESS_QUERY_INFORMATION
    handle = None
    try:
        handle = win32api.OpenProcess(access, False, pid)
        win32process.SetPriorityClass(handle, priority_class)
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        if handle:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def apply_profile(process_name: str, profile: str = "default") -> dict[str, Any]:
    process_name = process_name.lower()
    result: dict[str, Any] = {
        "profile": profile,
        "target_process": process_name,
        "applied": [],
        "failed": [],
    }

    target_priority = PRIORITY_MAP["high"]
    if profile == "safe":
        target_priority = PRIORITY_MAP["normal"]

    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if name != process_name:
            continue

        ok, err = _set_process_priority(proc.pid, target_priority)
        if ok:
            result["applied"].append(
                {
                    "pid": proc.pid,
                    "process": name,
                    "action": "set_priority",
                    "priority": "high" if target_priority == PRIORITY_MAP["high"] else "normal",
                }
            )
        else:
            result["failed"].append(
                {
                    "pid": proc.pid,
                    "process": name,
                    "action": "set_priority",
                    "error": err,
                }
            )

    if profile == "default":
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name not in SAFE_BACKGROUND_PROCESSES or name == process_name:
                continue

            ok, err = _set_process_priority(proc.pid, PRIORITY_MAP["below_normal"])
            if ok:
                result["applied"].append(
                    {
                        "pid": proc.pid,
                        "process": name,
                        "action": "set_priority",
                        "priority": "below_normal",
                    }
                )
            else:
                result["failed"].append(
                    {
                        "pid": proc.pid,
                        "process": name,
                        "action": "set_priority",
                        "error": err,
                    }
                )

    result["success"] = len(result["applied"]) > 0 and len(result["failed"]) == 0
    result["partial_success"] = len(result["applied"]) > 0 and len(result["failed"]) > 0
    return result
