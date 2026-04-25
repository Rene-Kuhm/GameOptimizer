from __future__ import annotations

from pathlib import Path

from app import discovery


def test_discover_executable_paths_ignores_launchers_and_tools(tmp_path: Path) -> None:
    install_dir = tmp_path / "Game"
    install_dir.mkdir()
    (install_dir / "GameLauncher.exe").write_text("launcher", encoding="utf-8")
    (install_dir / "Game.exe").write_text("game", encoding="utf-8")
    (install_dir / "EasyAntiCheat.exe").write_text("anti-cheat", encoding="utf-8")

    paths = discovery._discover_executable_paths(install_dir)

    assert [path.name for path in paths] == ["Game.exe"]


def test_discover_all_games_does_not_require_winreg(monkeypatch) -> None:
    monkeypatch.setattr(discovery, "winreg", None)
    monkeypatch.setattr(discovery, "REGISTRY_LOCATIONS", [])
    monkeypatch.setattr(discovery, "UNINSTALL_KEYS", [])

    games = discovery.discover_all_games()

    assert isinstance(games, list)
