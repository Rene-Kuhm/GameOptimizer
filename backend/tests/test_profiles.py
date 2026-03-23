from __future__ import annotations

import json
from pathlib import Path

from app.models import GameEntry
from app.profiles import OptimizationProfiles


def _write_profiles(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fallback_to_default_when_profile_missing(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.json"
    _write_profiles(profiles_path, {"profiles": {}})

    resolver = OptimizationProfiles(config_path=profiles_path)
    effective, settings, resolution = resolver.resolve_for_game(None, "unknown")

    assert effective == "default"
    assert settings["target_priority"] == "high"
    assert resolution["warnings"]


def test_override_matches_provider_and_executable_name(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        {
            "profiles": {
                "streaming": {
                    "target_priority": "normal",
                    "background_priority": None,
                    "cpu_affinity": [0, 1],
                }
            },
            "overrides": [
                {
                    "name": "valorant-streaming",
                    "match": {
                        "providers": ["steam"],
                        "executable_names": ["valorant.exe"],
                    },
                    "profile": "streaming",
                }
            ],
        },
    )

    resolver = OptimizationProfiles(config_path=profiles_path)
    game = GameEntry(
        id="steam:valorant",
        name="Valorant",
        source="Steam",
        executable_names=["valorant.exe"],
        metadata={"providers": ["Steam"]},
    )

    effective, settings, resolution = resolver.resolve_for_game(game, "default", process_name="valorant.exe")

    assert effective == "streaming"
    assert settings["cpu_affinity"] == [0, 1]
    assert resolution["override"] == "valorant-streaming"


def test_override_matches_path_contains(tmp_path: Path) -> None:
    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        {
            "overrides": [
                {
                    "name": "path-match",
                    "match": {"executable_paths_contains": ["\\games\\doom"]},
                    "settings": {"target_priority": "normal"},
                }
            ]
        },
    )

    resolver = OptimizationProfiles(config_path=profiles_path)
    game = GameEntry(
        id="scan:doom",
        name="DOOM",
        source="Scan",
        executable_names=["doom.exe"],
        metadata={"executables": [{"path": "C:/Games/DOOM/doom.exe"}]},
    )

    effective, settings, resolution = resolver.resolve_for_game(
        game,
        "default",
        process_name="doom.exe",
        process_path="C:/Games/DOOM/doom.exe",
    )

    assert effective == "default"
    assert settings["target_priority"] == "normal"
    assert resolution["override"] == "path-match"
