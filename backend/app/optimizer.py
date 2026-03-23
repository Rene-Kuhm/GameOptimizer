from __future__ import annotations

from typing import Any

import psutil
import win32api
import win32con
import win32process

from .logging_setup import get_logger


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

logger = get_logger(__name__)


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


def _get_process_priority(pid: int) -> tuple[int | None, str | None]:
    access = win32con.PROCESS_QUERY_INFORMATION
    handle = None
    try:
        handle = win32api.OpenProcess(access, False, pid)
        return win32process.GetPriorityClass(handle), None
    except Exception as exc:
        return None, str(exc)
    finally:
        if handle:
            try:
                win32api.CloseHandle(handle)
            except Exception:
                pass


def _set_process_affinity(pid: int, cpu_ids: list[int]) -> tuple[bool, str | None]:
    try:
        process = psutil.Process(pid)
        process.cpu_affinity(cpu_ids)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _get_process_affinity(pid: int) -> tuple[list[int] | None, str | None]:
    try:
        process = psutil.Process(pid)
        return process.cpu_affinity(), None
    except Exception as exc:
        return None, str(exc)


def _priority_name(priority_class: int | None) -> str | None:
    if priority_class is None:
        return None
    reverse = {value: key for key, value in PRIORITY_MAP.items()}
    return reverse.get(priority_class, str(priority_class))


def _sanitize_affinity(raw_value: Any) -> tuple[list[int] | None, str | None]:
    if raw_value is None:
        return None, None

    if not isinstance(raw_value, list):
        return None, "cpu_affinity must be a list of CPU indexes"

    if not raw_value:
        return None, "cpu_affinity cannot be empty"

    max_cpu = psutil.cpu_count(logical=True) or 0
    if max_cpu <= 0:
        return None, "unable to determine logical CPU count"

    sanitized: list[int] = []
    for value in raw_value:
        if not isinstance(value, int):
            return None, "cpu_affinity values must be integers"
        if value < 0 or value >= max_cpu:
            return None, f"cpu_affinity value {value} out of range (0-{max_cpu - 1})"
        if value not in sanitized:
            sanitized.append(value)

    if not sanitized:
        return None, "cpu_affinity resolved to empty set"

    return sanitized, None


def _legacy_profile_settings(profile: str) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    normalized_profile = profile
    if profile not in {"default", "safe"}:
        warnings.append(f"unknown profile '{profile}', falling back to default")
        normalized_profile = "default"

    settings: dict[str, Any] = {
        "target_priority": "high",
        "background_priority": "below_normal",
        "background_processes": SAFE_BACKGROUND_PROCESSES,
        "cpu_affinity": None,
    }
    if normalized_profile == "safe":
        settings["target_priority"] = "normal"
        settings["background_priority"] = None

    return normalized_profile, settings, warnings


def _new_action(
    *,
    pid: int,
    process_name: str,
    action: str,
    requested: Any = None,
    previous: Any = None,
) -> dict[str, Any]:
    return {
        "pid": pid,
        "process": process_name,
        "action": action,
        "requested": requested,
        "previous": previous,
        "current": None,
        "applied": False,
        "skipped": False,
        "reason": "not_attempted",
        "warnings": [],
    }


def _collect_targets(process_name: str, target_pid: int | None = None) -> list[dict[str, Any]]:
    process_name = process_name.lower()
    targets: list[dict[str, Any]] = []

    if target_pid is not None:
        try:
            proc = psutil.Process(target_pid)
            name = (proc.name() or "").lower()
            if process_name and name != process_name:
                logger.warning(
                    "target pid name mismatch pid=%s expected=%s actual=%s",
                    target_pid,
                    process_name,
                    name,
                )
            targets.append({"pid": proc.pid, "name": name})
            return targets
        except Exception as exc:
            logger.warning("target pid not available pid=%s error=%s", target_pid, exc)
            return targets

    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info.get("name") or "").lower()
        if name == process_name:
            targets.append({"pid": proc.pid, "name": name})
    return targets


def apply_profile(
    process_name: str,
    profile: str = "default",
    *,
    target_pid: int | None = None,
    profile_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    process_name = process_name.lower()
    legacy_profile, legacy_settings, legacy_warnings = _legacy_profile_settings(profile)
    settings = dict(profile_config or legacy_settings)
    effective_profile = profile if profile_config is not None else legacy_profile

    result: dict[str, Any] = {
        "profile": profile,
        "effective_profile": effective_profile,
        "target_process": process_name,
        "target_pid": target_pid,
        "actions": [],
        "applied": [],
        "failed": [],
        "skipped": [],
        "warnings": legacy_warnings,
        "session_changes": [],
    }

    target_priority_name = settings.get("target_priority")
    target_priority = PRIORITY_MAP.get(target_priority_name) if isinstance(target_priority_name, str) else None
    if target_priority is None:
        result["warnings"].append(f"invalid target_priority '{target_priority_name}', skipping")

    affinity, affinity_error = _sanitize_affinity(settings.get("cpu_affinity"))
    if affinity_error:
        result["warnings"].append(affinity_error)

    session_changes_by_pid: dict[int, dict[str, Any]] = {}

    targets = _collect_targets(process_name, target_pid=target_pid)
    if not targets:
        result["warnings"].append("no target process found")

    for target in targets:
        pid = target["pid"]
        name = target["name"]

        if target_priority is not None:
            action = _new_action(
                pid=pid,
                process_name=name,
                action="set_priority",
                requested=target_priority_name,
            )
            current_priority, current_priority_err = _get_process_priority(pid)
            action["previous"] = _priority_name(current_priority)
            if current_priority_err:
                action["reason"] = current_priority_err
            elif current_priority == target_priority:
                action["skipped"] = True
                action["current"] = _priority_name(current_priority)
                action["reason"] = "already_set"
            else:
                ok, err = _set_process_priority(pid, target_priority)
                if ok:
                    action["applied"] = True
                    action["current"] = target_priority_name
                    action["reason"] = "applied"
                    session_changes_by_pid.setdefault(
                        pid,
                        {
                            "pid": pid,
                            "process": name,
                            "priority_before": _priority_name(current_priority),
                            "affinity_before": None,
                            "priority_changed": False,
                            "affinity_changed": False,
                        },
                    )
                    session_changes_by_pid[pid]["priority_changed"] = True
                else:
                    action["reason"] = err or "failed"

            result["actions"].append(action)

        if affinity is not None:
            action = _new_action(
                pid=pid,
                process_name=name,
                action="set_cpu_affinity",
                requested=affinity,
            )
            current_affinity, current_affinity_err = _get_process_affinity(pid)
            action["previous"] = current_affinity
            if current_affinity_err:
                action["reason"] = current_affinity_err
            elif sorted(current_affinity or []) == sorted(affinity):
                action["skipped"] = True
                action["current"] = current_affinity
                action["reason"] = "already_set"
            else:
                ok, err = _set_process_affinity(pid, affinity)
                if ok:
                    action["applied"] = True
                    action["current"] = affinity
                    action["reason"] = "applied"
                    session_changes_by_pid.setdefault(
                        pid,
                        {
                            "pid": pid,
                            "process": name,
                            "priority_before": None,
                            "affinity_before": current_affinity,
                            "priority_changed": False,
                            "affinity_changed": False,
                        },
                    )
                    if session_changes_by_pid[pid].get("affinity_before") is None:
                        session_changes_by_pid[pid]["affinity_before"] = current_affinity
                    session_changes_by_pid[pid]["affinity_changed"] = True
                else:
                    action["reason"] = err or "failed"

            result["actions"].append(action)

    background_priority_name = settings.get("background_priority")
    background_priority = (
        PRIORITY_MAP.get(background_priority_name)
        if isinstance(background_priority_name, str) and background_priority_name
        else None
    )
    background_processes = {
        str(item).lower() for item in settings.get("background_processes", SAFE_BACKGROUND_PROCESSES) if item
    }
    if background_priority_name and background_priority is None:
        result["warnings"].append(f"invalid background_priority '{background_priority_name}', skipping")

    if background_priority is not None:
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name not in background_processes:
                continue
            if name == process_name and (target_pid is None or proc.pid == target_pid):
                continue

            action = _new_action(
                pid=proc.pid,
                process_name=name,
                action="set_background_priority",
                requested=background_priority_name,
            )

            current_priority, current_priority_err = _get_process_priority(proc.pid)
            action["previous"] = _priority_name(current_priority)
            if current_priority_err:
                action["reason"] = current_priority_err
            elif current_priority == background_priority:
                action["skipped"] = True
                action["current"] = _priority_name(current_priority)
                action["reason"] = "already_set"
            else:
                ok, err = _set_process_priority(proc.pid, background_priority)
                if ok:
                    action["applied"] = True
                    action["current"] = background_priority_name
                    action["reason"] = "applied"
                    session_changes_by_pid.setdefault(
                        proc.pid,
                        {
                            "pid": proc.pid,
                            "process": name,
                            "priority_before": _priority_name(current_priority),
                            "affinity_before": None,
                            "priority_changed": False,
                            "affinity_changed": False,
                        },
                    )
                    session_changes_by_pid[proc.pid]["priority_changed"] = True
                else:
                    action["reason"] = err or "failed"

            result["actions"].append(action)

    for action in result["actions"]:
        if action["applied"]:
            result["applied"].append(action)
        elif action["skipped"]:
            result["skipped"].append(action)
        else:
            result["failed"].append(action)

    result["session_changes"] = list(session_changes_by_pid.values())
    result["success"] = len(result["applied"]) > 0 and len(result["failed"]) == 0
    result["partial_success"] = len(result["applied"]) > 0 and len(result["failed"]) > 0

    logger.info(
        "optimization_result profile=%s target=%s pid=%s applied=%s skipped=%s failed=%s",
        result["effective_profile"],
        process_name,
        target_pid,
        len(result["applied"]),
        len(result["skipped"]),
        len(result["failed"]),
    )
    return result


def rollback_session(session_changes: list[dict[str, Any]] | None) -> dict[str, Any]:
    changes = session_changes or []
    result: dict[str, Any] = {
        "actions": [],
        "applied": [],
        "failed": [],
        "skipped": [],
        "warnings": [],
    }

    if not changes:
        result["warnings"].append("no session changes to rollback")
        result["success"] = True
        return result

    for change in changes:
        pid = int(change.get("pid", 0) or 0)
        process_name = str(change.get("process", "unknown"))
        if pid <= 0:
            continue

        try:
            psutil.Process(pid)
        except Exception:
            action = _new_action(pid=pid, process_name=process_name, action="rollback_process")
            action["skipped"] = True
            action["reason"] = "process_not_found"
            result["actions"].append(action)
            continue

        if change.get("priority_changed"):
            priority_before = str(change.get("priority_before") or "")
            target_priority = PRIORITY_MAP.get(priority_before) if priority_before else None
            action = _new_action(
                pid=pid,
                process_name=process_name,
                action="rollback_priority",
                requested=priority_before,
            )
            if target_priority is None:
                action["skipped"] = True
                action["reason"] = "missing_or_invalid_previous_priority"
            else:
                current_priority, current_priority_err = _get_process_priority(pid)
                action["previous"] = _priority_name(current_priority)
                if current_priority_err:
                    action["reason"] = current_priority_err
                elif current_priority == target_priority:
                    action["skipped"] = True
                    action["current"] = _priority_name(current_priority)
                    action["reason"] = "already_restored"
                else:
                    ok, err = _set_process_priority(pid, target_priority)
                    if ok:
                        action["applied"] = True
                        action["current"] = priority_before
                        action["reason"] = "restored"
                    else:
                        action["reason"] = err or "failed"
            result["actions"].append(action)

        if change.get("affinity_changed"):
            affinity_before = change.get("affinity_before")
            action = _new_action(
                pid=pid,
                process_name=process_name,
                action="rollback_cpu_affinity",
                requested=affinity_before,
            )
            affinity, affinity_error = _sanitize_affinity(affinity_before)
            if affinity_error:
                action["skipped"] = True
                action["reason"] = affinity_error
            else:
                current_affinity, current_affinity_err = _get_process_affinity(pid)
                action["previous"] = current_affinity
                if current_affinity_err:
                    action["reason"] = current_affinity_err
                elif sorted(current_affinity or []) == sorted(affinity or []):
                    action["skipped"] = True
                    action["current"] = current_affinity
                    action["reason"] = "already_restored"
                else:
                    ok, err = _set_process_affinity(pid, affinity or [])
                    if ok:
                        action["applied"] = True
                        action["current"] = affinity
                        action["reason"] = "restored"
                    else:
                        action["reason"] = err or "failed"
            result["actions"].append(action)

    for action in result["actions"]:
        if action["applied"]:
            result["applied"].append(action)
        elif action["skipped"]:
            result["skipped"].append(action)
        else:
            result["failed"].append(action)

    result["success"] = len(result["failed"]) == 0
    logger.info(
        "rollback_result applied=%s skipped=%s failed=%s",
        len(result["applied"]),
        len(result["skipped"]),
        len(result["failed"]),
    )
    return result
