from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

try:  # pragma: no cover - import behavior differs by platform
    import winreg
except Exception:  # pragma: no cover - non-Windows compatibility
    winreg = None

from .executable_meta import get_executable_metadata_cache
from .models import GameEntry


if winreg is not None:
    REGISTRY_LOCATIONS = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]

    UNINSTALL_KEYS = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
else:
    REGISTRY_LOCATIONS = []
    UNINSTALL_KEYS = []

SKIP_EXE_TOKENS = {
    "unins",
    "setup",
    "crash",
    "updater",
    "launcher",
    "redis",
    "vc_redist",
    "helper",
    "service",
    "anticheat",
    "easyanticheat",
    "eadesktop",
    "ubisoftconnect",
    "battle.net",
}

KNOWN_IGNORED_PROCESS_NAMES = {
    "steam.exe",
    "steamwebhelper.exe",
    "epicgameslauncher.exe",
    "gog galaxy.exe",
    "galaxyclient.exe",
    "eadesktop.exe",
    "origin.exe",
    "ubisoftconnect.exe",
    "battle.net.exe",
    "discordoverlay.exe",
    "gamebar.exe",
    "gamebarpresencewriter.exe",
    "easyanticheat.exe",
    "easyanticheat_eos.exe",
    "beservice.exe",
}

DEFAULT_GENERIC_ROOTS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Games",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Games",
    Path(r"C:\Games"),
    Path(r"D:\Games"),
    Path(r"E:\Games"),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Epic Games",
    Path(r"C:\XboxGames"),
]

XBOX_ROOTS = [
    Path(r"C:\XboxGames"),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "XboxGames",
]


def _read_reg_value(root: int, subkey: str, value_name: str) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return str(value)
    except OSError:
        return None


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_exe_name(name: str) -> str:
    return os.path.basename(name).lower()


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return str(Path(path).resolve(strict=False)).lower()
    except OSError:
        return str(Path(path)).lower()


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned or "unknown"


def _parse_vdf_value(content: str, key: str) -> str | None:
    pattern = rf'"{re.escape(key)}"\s+"([^"]+)"'
    match = re.search(pattern, content)
    return match.group(1).strip() if match else None


def _is_probably_launcher_or_tool(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in SKIP_EXE_TOKENS)


def _discover_executable_paths(install_dir: Path, max_results: int = 8, max_depth: int = 3) -> list[Path]:
    if not install_dir.exists() or not install_dir.is_dir():
        return []

    discovered: list[Path] = []
    base_depth = len(install_dir.parts)

    try:
        for root, dirs, files in os.walk(install_dir):
            current = Path(root)
            current_depth = len(current.parts) - base_depth
            if current_depth >= max_depth:
                dirs[:] = []

            dirs[:] = [d for d in dirs if "windowsapps" not in d.lower()]

            for file_name in files:
                if not file_name.lower().endswith(".exe"):
                    continue
                if _is_probably_launcher_or_tool(file_name):
                    continue
                discovered.append(current / file_name)
                if len(discovered) >= max_results:
                    return discovered
    except (OSError, PermissionError):
        return []

    return discovered


def _build_entry(
    *,
    game_id: str,
    name: str,
    source: str,
    install_dir: str | None,
    executable_paths: list[Path],
    extra_metadata: dict[str, Any] | None = None,
) -> GameEntry:
    metadata_cache = get_executable_metadata_cache()
    executable_meta: list[dict[str, Any]] = []
    for index, path in enumerate(executable_paths):
        executable_meta.append(
            metadata_cache.collect(
                path,
                include_hash=False,
                include_signature=index == 0,
            )
        )
    executable_names = sorted({_normalize_exe_name(item.get("path", "")) for item in executable_meta if item.get("path")})

    metadata: dict[str, Any] = {
        "providers": [source],
        "executables": executable_meta,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return GameEntry(
        id=game_id,
        name=name,
        source=source,
        install_dir=install_dir,
        executable_names=executable_names,
        metadata=metadata,
    )


def _steam_install_path() -> str | None:
    for root, subkey, value_name in REGISTRY_LOCATIONS:
        value = _read_reg_value(root, subkey, value_name)
        if value:
            return value
    return None


def steam_libraries() -> list[Path]:
    install_path = _steam_install_path()
    if not install_path:
        return []

    steam_root = Path(install_path)
    libraries = [steam_root]
    lib_file = steam_root / "steamapps" / "libraryfolders.vdf"
    if not lib_file.exists():
        return libraries

    try:
        content = lib_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libraries

    path_matches = re.findall(r'"path"\s+"([^"]+)"', content)
    old_matches = re.findall(r'^\s*"\d+"\s+"([^"]+)"\s*$', content, flags=re.MULTILINE)

    for raw in [*path_matches, *old_matches]:
        candidate = Path(raw.replace("\\\\", "\\"))
        if candidate.exists() and candidate not in libraries:
            libraries.append(candidate)

    return libraries


def _epic_manifest_paths() -> Iterable[Path]:
    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
    ]

    reg_hint = None
    if winreg is not None:
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


def _iter_uninstall_entries() -> Iterable[dict[str, str]]:
    for root, subkey in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(root, subkey) as key:
                index = 0
                while True:
                    child = winreg.EnumKey(key, index)
                    index += 1
                    try:
                        with winreg.OpenKey(key, child) as item:
                            values: dict[str, str] = {}
                            for value_name in ["DisplayName", "InstallLocation", "Publisher", "DisplayIcon"]:
                                try:
                                    raw, _ = winreg.QueryValueEx(item, value_name)
                                    values[value_name] = str(raw)
                                except OSError:
                                    continue
                            if values.get("DisplayName"):
                                yield values
                    except OSError:
                        continue
        except OSError:
            continue


def _display_icon_to_path(display_icon: str | None) -> Path | None:
    if not display_icon:
        return None
    candidate = display_icon.strip().strip('"')
    if "," in candidate:
        candidate = candidate.split(",", 1)[0].strip().strip('"')
    path = Path(candidate)
    return path if path.exists() else None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass
class ProviderContext:
    steam_library_paths: list[Path]


class DiscoveryProvider(Protocol):
    id: str

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        ...


class SteamProvider:
    id = "steam"

    def _read_manifest(self, manifest_file: Path) -> dict[str, str]:
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

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        games: list[GameEntry] = []

        for library in context.steam_library_paths:
            steamapps = library / "steamapps"
            if not steamapps.exists():
                continue

            for manifest in steamapps.glob("appmanifest_*.acf"):
                parsed = self._read_manifest(manifest)
                if not parsed:
                    continue

                install_dir = steamapps / "common" / parsed["installdir"]
                executable_paths = _discover_executable_paths(install_dir)
                games.append(
                    _build_entry(
                        game_id=f"steam:{parsed['appid']}",
                        name=parsed["name"],
                        source="Steam",
                        install_dir=str(install_dir),
                        executable_paths=executable_paths,
                        extra_metadata={"appid": parsed["appid"]},
                    )
                )

        return games


class EpicProvider:
    id = "epic"

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        games: list[GameEntry] = []

        for manifests_root in _epic_manifest_paths():
            for item_file in manifests_root.glob("*.item"):
                payload = _load_json(item_file)
                if not payload:
                    continue

                display_name = str(payload.get("DisplayName") or payload.get("AppName") or "").strip()
                if not display_name:
                    continue

                install_dir_value = payload.get("InstallLocation")
                install_dir = Path(install_dir_value) if install_dir_value else None
                app_name = str(payload.get("AppName") or display_name)

                games.append(
                    _build_entry(
                        game_id=f"epic:{_slug(app_name)}",
                        name=display_name,
                        source="Epic",
                        install_dir=str(install_dir) if install_dir else None,
                        executable_paths=_discover_executable_paths(install_dir) if install_dir else [],
                        extra_metadata={"app_name": app_name},
                    )
                )

        return games


class GOGProvider:
    id = "gog"

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        roots = [
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "GOG.com" / "Galaxy" / "storage",
        ]
        games: list[GameEntry] = []
        seen_paths: set[str] = set()

        for root in roots:
            if not root.exists():
                continue

            scanned = 0
            for json_file in root.glob("**/*.json"):
                scanned += 1
                if scanned > 600:
                    break
                payload = _load_json(json_file)
                if not payload:
                    continue

                install_path = payload.get("installPath") or payload.get("path")
                title = payload.get("title") or payload.get("name") or payload.get("gameName")
                game_id = payload.get("gameId") or payload.get("id") or payload.get("productId")
                if not install_path or not title:
                    continue

                normalized_path = _normalize_path(str(install_path))
                if not normalized_path or normalized_path in seen_paths:
                    continue

                seen_paths.add(normalized_path)
                install_dir = Path(str(install_path))
                games.append(
                    _build_entry(
                        game_id=f"gog:{_slug(str(game_id or title))}",
                        name=str(title),
                        source="GOG",
                        install_dir=str(install_dir),
                        executable_paths=_discover_executable_paths(install_dir),
                        extra_metadata={"manifest": str(json_file)},
                    )
                )

        for entry in _iter_uninstall_entries():
            publisher = entry.get("Publisher", "").lower()
            if "gog" not in publisher:
                continue
            name = entry.get("DisplayName", "").strip()
            if not name:
                continue
            install = entry.get("InstallLocation", "").strip()
            install_dir = Path(install) if install else None
            games.append(
                _build_entry(
                    game_id=f"gog:uninstall:{_slug(name)}",
                    name=name,
                    source="GOG",
                    install_dir=str(install_dir) if install_dir else None,
                    executable_paths=_discover_executable_paths(install_dir) if install_dir else [],
                    extra_metadata={"registry_publisher": entry.get("Publisher", "")},
                )
            )

        return games


class PublisherRegistryProvider:
    def __init__(self, source: str, provider_id: str, tokens: tuple[str, ...]) -> None:
        self.source = source
        self.id = provider_id
        self.tokens = tuple(token.lower() for token in tokens)

    def _is_match(self, entry: dict[str, str]) -> bool:
        combined = f"{entry.get('Publisher', '')} {entry.get('DisplayName', '')}".lower()
        return any(token in combined for token in self.tokens)

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        games: list[GameEntry] = []
        for entry in _iter_uninstall_entries():
            if not self._is_match(entry):
                continue

            name = entry.get("DisplayName", "").strip()
            if not name:
                continue

            install_location = entry.get("InstallLocation", "").strip()
            executable_paths: list[Path] = []
            install_dir: Path | None = Path(install_location) if install_location else None
            if install_dir and install_dir.exists():
                executable_paths = _discover_executable_paths(install_dir)

            icon_path = _display_icon_to_path(entry.get("DisplayIcon"))
            if icon_path and icon_path.suffix.lower() == ".exe":
                executable_paths.append(icon_path)

            game_id = f"{self.id}:registry:{_slug(name)}"
            games.append(
                _build_entry(
                    game_id=game_id,
                    name=name,
                    source=self.source,
                    install_dir=str(install_dir) if install_dir else None,
                    executable_paths=executable_paths,
                    extra_metadata={"registry_publisher": entry.get("Publisher", "")},
                )
            )

        return games


class XboxProvider:
    id = "xbox"

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        games: list[GameEntry] = []
        seen_paths: set[str] = set()
        for root in XBOX_ROOTS:
            if not root.exists() or not root.is_dir():
                continue

            for game_dir in root.iterdir():
                if not game_dir.is_dir():
                    continue
                content_dir = game_dir / "Content"
                target = content_dir if content_dir.exists() else game_dir
                executable_paths = _discover_executable_paths(target, max_results=4, max_depth=2)
                if not executable_paths:
                    continue

                normalized_install = _normalize_path(str(target))
                if normalized_install and normalized_install in seen_paths:
                    continue
                if normalized_install:
                    seen_paths.add(normalized_install)

                games.append(
                    _build_entry(
                        game_id=f"xbox:{_slug(game_dir.name)}",
                        name=game_dir.name,
                        source="Xbox",
                        install_dir=str(target),
                        executable_paths=executable_paths,
                        extra_metadata={"detection": "filesystem"},
                    )
                )

        return games


class GenericFilesystemProvider:
    id = "generic-scan"

    def _scan_roots(self, context: ProviderContext) -> list[Path]:
        roots = list(DEFAULT_GENERIC_ROOTS)
        roots.extend(context.steam_library_paths)

        custom_env = os.environ.get("GAME_OPTIMIZER_GAME_PATHS", "").strip()
        if custom_env:
            for raw in custom_env.split(";"):
                value = raw.strip().strip('"')
                if value:
                    roots.append(Path(value))

        deduped: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            normalized = _normalize_path(str(root))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if root.exists() and root.is_dir():
                deduped.append(root)
        return deduped

    def discover(self, context: ProviderContext) -> list[GameEntry]:
        games: list[GameEntry] = []
        for root in self._scan_roots(context):
            try:
                candidates = [item for item in root.iterdir() if item.is_dir()]
            except OSError:
                continue

            for candidate in candidates[:250]:
                executable_paths = _discover_executable_paths(candidate, max_results=3, max_depth=2)
                if not executable_paths:
                    continue

                game_name = candidate.name
                if _is_probably_launcher_or_tool(game_name):
                    continue

                games.append(
                    _build_entry(
                        game_id=f"scan:{_slug(str(candidate))}",
                        name=game_name,
                        source="Scan",
                        install_dir=str(candidate),
                        executable_paths=executable_paths,
                        extra_metadata={"scan_root": str(root)},
                    )
                )

        return games


def _merge_games(existing: GameEntry, incoming: GameEntry) -> GameEntry:
    executable_names = sorted(set(existing.executable_names) | set(incoming.executable_names))
    providers = set(existing.metadata.get("providers", [existing.source]))
    providers.update(incoming.metadata.get("providers", [incoming.source]))

    merged_execs: dict[str, dict[str, Any]] = {}
    for payload in existing.metadata.get("executables", []):
        normalized = _normalize_path(payload.get("path"))
        if normalized:
            merged_execs[normalized] = payload
    for payload in incoming.metadata.get("executables", []):
        normalized = _normalize_path(payload.get("path"))
        if normalized and normalized not in merged_execs:
            merged_execs[normalized] = payload

    metadata = {**existing.metadata}
    for key, value in incoming.metadata.items():
        if key in {"providers", "executables"}:
            continue
        metadata.setdefault(key, value)

    metadata["providers"] = sorted(providers)
    metadata["executables"] = list(merged_execs.values())

    return GameEntry(
        id=existing.id,
        name=existing.name,
        source=existing.source,
        install_dir=existing.install_dir or incoming.install_dir,
        executable_names=executable_names,
        metadata=metadata,
    )


def _deduplicate_games(games: list[GameEntry]) -> list[GameEntry]:
    deduped: dict[str, GameEntry] = {}

    for game in games:
        normalized_title = _normalize_text(game.name)
        executable_items = game.metadata.get("executables", []) if isinstance(game.metadata, dict) else []

        keys: list[str] = []
        for payload in executable_items:
            normalized_path = _normalize_path(payload.get("path"))
            if normalized_path:
                keys.append(f"{normalized_title}|{normalized_path}")

        if not keys:
            normalized_install = _normalize_path(game.install_dir)
            keys = [f"{normalized_title}|{normalized_install or game.id}"]

        existing_key = next((key for key in keys if key in deduped), None)
        if existing_key:
            deduped[existing_key] = _merge_games(deduped[existing_key], game)
            for key in keys:
                deduped[key] = deduped[existing_key]
            continue

        representative = keys[0]
        deduped[representative] = game
        for key in keys[1:]:
            deduped[key] = game

    unique: dict[str, GameEntry] = {}
    for game in deduped.values():
        unique[game.id] = game
    return list(unique.values())


def discover_all_games() -> list[GameEntry]:
    context = ProviderContext(steam_library_paths=steam_libraries())
    providers: list[DiscoveryProvider] = [
        SteamProvider(),
        EpicProvider(),
        GOGProvider(),
        PublisherRegistryProvider("Battle.net", "bnet", ("blizzard", "battle.net")),
        PublisherRegistryProvider("Ubisoft", "ubisoft", ("ubisoft",)),
        PublisherRegistryProvider("EA", "ea", ("electronic arts", "ea app", "origin")),
        XboxProvider(),
        GenericFilesystemProvider(),
    ]

    discovered: list[GameEntry] = []
    for provider in providers:
        try:
            discovered.extend(provider.discover(context))
        except Exception:
            continue

    all_games = _deduplicate_games(discovered)
    all_games.sort(key=lambda game: (game.source, game.name.lower()))
    return all_games
