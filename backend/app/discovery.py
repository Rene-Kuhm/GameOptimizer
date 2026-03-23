from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

import winreg

from .models import GameEntry


REGISTRY_LOCATIONS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
]


def _read_reg_value(root: int, subkey: str, value_name: str) -> str | None:
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return str(value)
    except OSError:
        return None


def _parse_vdf_value(content: str, key: str) -> str | None:
    pattern = rf'"{re.escape(key)}"\s+"([^"]+)"'
    match = re.search(pattern, content)
    return match.group(1).strip() if match else None


def _normalize_exe_name(name: str) -> str:
    return os.path.basename(name).lower()


def _is_probably_launcher_or_tool(name: str) -> bool:
    lowered = name.lower()
    skip_tokens = [
        "unins",
        "setup",
        "crash",
        "updater",
        "launcher",
        "redis",
    ]
    return any(token in lowered for token in skip_tokens)


def _discover_executables(install_dir: Path, max_results: int = 5) -> list[str]:
    if not install_dir.exists() or not install_dir.is_dir():
        return []

    discovered: list[str] = []
    try:
        for exe in install_dir.rglob("*.exe"):
            name = exe.name
            if _is_probably_launcher_or_tool(name):
                continue
            discovered.append(_normalize_exe_name(name))
            if len(discovered) >= max_results:
                break
    except (OSError, PermissionError):
        return []

    return sorted(set(discovered))


def _steam_install_path() -> str | None:
    for root, subkey, value_name in REGISTRY_LOCATIONS:
        value = _read_reg_value(root, subkey, value_name)
        if value:
            return value
    return None


def _steam_libraries(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    lib_file = steam_root / "steamapps" / "libraryfolders.vdf"
    if not lib_file.exists():
        return libraries

    try:
        content = lib_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libraries

    # Supports both old and newer VDF formats.
    path_matches = re.findall(r'"path"\s+"([^"]+)"', content)
    old_matches = re.findall(r'^\s*"\d+"\s+"([^"]+)"\s*$', content, flags=re.MULTILINE)

    for raw in [*path_matches, *old_matches]:
        candidate = Path(raw.replace("\\\\", "\\"))
        if candidate.exists() and candidate not in libraries:
            libraries.append(candidate)

    return libraries


def _read_steam_manifest(manifest_file: Path) -> dict[str, str]:
    try:
        content = manifest_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    app_id = _parse_vdf_value(content, "appid")
    name = _parse_vdf_value(content, "name")
    install_dir = _parse_vdf_value(content, "installdir")
    if not app_id or not name:
        return {}

    return {
        "appid": app_id,
        "name": name,
        "installdir": install_dir or "",
    }


def discover_steam_games() -> list[GameEntry]:
    install_path = _steam_install_path()
    if not install_path:
        return []

    root = Path(install_path)
    games: list[GameEntry] = []

    for lib in _steam_libraries(root):
        steamapps = lib / "steamapps"
        if not steamapps.exists():
            continue

        for manifest in steamapps.glob("appmanifest_*.acf"):
            parsed = _read_steam_manifest(manifest)
            if not parsed:
                continue

            common_dir = steamapps / "common" / parsed["installdir"]
            executables = _discover_executables(common_dir)
            games.append(
                GameEntry(
                    id=f"steam:{parsed['appid']}",
                    name=parsed["name"],
                    source="Steam",
                    install_dir=str(common_dir),
                    executable_names=executables,
                    metadata={"appid": parsed["appid"]},
                )
            )

    return games


def _epic_manifest_paths() -> Iterable[Path]:
    # ProgramData path is launcher-standard and does not require registry success.
    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
    ]

    reg_hint = _read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Epic Games\EpicGamesLauncher",
        "AppDataPath",
    )
    if reg_hint:
        roots.append(Path(reg_hint) / "Data" / "Manifests")

    for root in roots:
        if root.exists() and root.is_dir():
            yield root


def discover_epic_games() -> list[GameEntry]:
    games: list[GameEntry] = []

    for manifests_root in _epic_manifest_paths():
        for item_file in manifests_root.glob("*.item"):
            try:
                payload = json.loads(item_file.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, json.JSONDecodeError):
                continue

            display_name = payload.get("DisplayName") or payload.get("AppName")
            if not display_name:
                continue

            install_dir = payload.get("InstallLocation")
            app_name = payload.get("AppName", display_name)
            executables = []
            if install_dir:
                executables = _discover_executables(Path(install_dir))

            games.append(
                GameEntry(
                    id=f"epic:{app_name}",
                    name=display_name,
                    source="Epic",
                    install_dir=install_dir,
                    executable_names=executables,
                    metadata={"app_name": app_name},
                )
            )

    dedup: dict[str, GameEntry] = {}
    for game in games:
        dedup[game.id] = game
    return list(dedup.values())


def discover_all_games() -> list[GameEntry]:
    steam = discover_steam_games()
    epic = discover_epic_games()

    all_games = steam + epic
    all_games.sort(key=lambda game: (game.source, game.name.lower()))
    return all_games
