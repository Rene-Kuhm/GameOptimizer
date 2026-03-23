from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .logging_setup import get_logger
from .models import GameEntry

logger = get_logger(__name__)

DEFAULT_PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "default": {
        "target_priority": "high",
        "background_priority": "below_normal",
        "background_processes": [
            "chrome.exe",
            "msedge.exe",
            "discord.exe",
            "spotify.exe",
            "onedrive.exe",
        ],
        "cpu_affinity": None,
    },
    "safe": {
        "target_priority": "normal",
        "background_priority": None,
        "background_processes": [],
        "cpu_affinity": None,
    },
}


def _normalize_text(value: str) -> str:
    return value.lower().strip()


def _normalize_path_text(value: str) -> str:
    return _normalize_text(value).replace("\\", "/")


def _match_override(
    match: Any,
    *,
    executable_names: set[str],
    executable_paths: set[str],
    providers: set[str],
) -> bool:
    if not isinstance(match, dict):
        return False

    override_exec_names = {_normalize_text(item) for item in match.get("executable_names", []) if item}
    if override_exec_names and not override_exec_names.intersection(executable_names):
        return False

    override_paths_contains = [_normalize_path_text(item) for item in match.get("executable_paths_contains", []) if item]
    if override_paths_contains and not any(
        needle in target_path
        for needle in override_paths_contains
        for target_path in executable_paths
    ):
        return False

    override_providers = {_normalize_text(item) for item in match.get("providers", []) if item}
    if override_providers and not override_providers.intersection(providers):
        return False

    return True


class OptimizationProfiles:
    def __init__(self, config_path: Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "config" / "profiles.json"
        self.config_path = config_path or default_path
        self.profiles = {name: dict(payload) for name, payload in DEFAULT_PROFILE_DEFINITIONS.items()}
        self.overrides: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            logger.info("profiles config missing path=%s using defaults", self.config_path)
            return

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("profiles config invalid path=%s error=%s", self.config_path, exc)
            return

        payload_profiles = raw.get("profiles") if isinstance(raw, dict) else None
        if isinstance(payload_profiles, dict):
            for profile_name, settings in payload_profiles.items():
                if not isinstance(profile_name, str) or not isinstance(settings, dict):
                    continue
                if profile_name not in self.profiles:
                    self.profiles[profile_name] = {}
                merged = dict(self.profiles[profile_name])
                merged.update(settings)
                self.profiles[profile_name] = merged

        payload_overrides = raw.get("overrides") if isinstance(raw, dict) else None
        if isinstance(payload_overrides, list):
            self.overrides = [item for item in payload_overrides if isinstance(item, dict)]

    def get_profile(self, profile_name: str) -> tuple[str, dict[str, Any], list[str]]:
        warnings: list[str] = []
        effective_profile = profile_name
        if profile_name not in self.profiles:
            warnings.append(f"profile '{profile_name}' not found, falling back to default")
            effective_profile = "default"
        return effective_profile, dict(self.profiles[effective_profile]), warnings

    def resolve_for_game(
        self,
        game: GameEntry | None,
        requested_profile: str,
        *,
        process_name: str | None = None,
        process_path: str | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        effective_profile, profile_settings, warnings = self.get_profile(requested_profile)
        resolution: dict[str, Any] = {
            "requested_profile": requested_profile,
            "effective_profile": effective_profile,
            "override": None,
            "warnings": warnings,
        }

        if game is None:
            return effective_profile, profile_settings, resolution

        executable_names = {_normalize_text(name) for name in game.executable_names if name}
        if process_name:
            executable_names.add(_normalize_text(process_name))

        executable_paths: set[str] = set()
        if isinstance(game.metadata, dict):
            for executable in game.metadata.get("executables", []):
                if not isinstance(executable, dict):
                    continue
                path = executable.get("path")
                if isinstance(path, str) and path:
                    executable_paths.add(_normalize_path_text(path))
        if process_path:
            executable_paths.add(_normalize_path_text(process_path))

        providers = {_normalize_text(game.source)}
        if isinstance(game.metadata, dict):
            providers.update(_normalize_text(item) for item in game.metadata.get("providers", []) if item)

        for override in self.overrides:
            if override.get("disabled") is True:
                continue

            match = override.get("match")
            if not _match_override(
                match,
                executable_names=executable_names,
                executable_paths=executable_paths,
                providers=providers,
            ):
                continue

            override_name = override.get("name") or "unnamed_override"
            resolution["override"] = override_name

            override_profile_name = override.get("profile")
            if isinstance(override_profile_name, str) and override_profile_name:
                if override_profile_name in self.profiles:
                    effective_profile = override_profile_name
                    profile_settings = dict(self.profiles[override_profile_name])
                    resolution["effective_profile"] = override_profile_name
                else:
                    resolution["warnings"].append(
                        f"override '{override_name}' references unknown profile '{override_profile_name}'"
                    )

            override_settings = override.get("settings")
            if isinstance(override_settings, dict):
                merged_settings = dict(profile_settings)
                merged_settings.update(override_settings)
                profile_settings = merged_settings

            break

        return effective_profile, profile_settings, resolution
