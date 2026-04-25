from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import GameEntry
from app.watcher import ActiveGame, GameWatcher, ProcessSnapshot


@pytest.fixture
def steam_game() -> GameEntry:
    return GameEntry(
        id="steam:1245620",
        name="Elden Ring",
        source="Steam",
        executable_names=["eldenring.exe"],
        metadata={"providers": ["Steam"]},
    )


def test_match_ignores_known_launcher_process(steam_game: GameEntry) -> None:
    watcher = GameWatcher(optimization_delay_seconds=0)
    profile = watcher._build_profile(steam_game)

    result = watcher._match(
        ProcessSnapshot(pid=1, name="steam.exe", executable_path="C:/Steam/steam.exe"),
        profile,
    )

    assert result.matched is False
    assert result.reason == "ignored_process"


def test_child_game_process_from_launcher_is_matched(steam_game: GameEntry) -> None:
    watcher = GameWatcher(optimization_delay_seconds=0)
    profile = watcher._build_profile(steam_game)

    result = watcher._match(
        ProcessSnapshot(
            pid=20,
            name="eldenring.exe",
            executable_path="C:/Steam/steamapps/common/ELDEN RING/Game/eldenring.exe",
            parent_pid=10,
            parent_name="steam.exe",
        ),
        profile,
    )

    assert result.matched is True
    assert "launcher_child_process" in result.matched_via


def test_optimization_delay_waits_before_starting(steam_game: GameEntry) -> None:
    watcher = GameWatcher(optimization_delay_seconds=5)
    active = ActiveGame(
        game=steam_game,
        pid=20,
        process_name="eldenring.exe",
        process_exe="c:/game/eldenring.exe",
        confidence=0.9,
        reason="exact_executable_name",
        matched_via=["exact_executable_name"],
    )

    assert watcher._ready_started_games({steam_game.id: active}) == []

    watcher._pending_by_id[steam_game.id]["first_seen_at"] = datetime.now(timezone.utc) - timedelta(seconds=6)

    assert watcher._ready_started_games({steam_game.id: active}) == [active]


def test_stop_restores_active_sessions(monkeypatch: pytest.MonkeyPatch, steam_game: GameEntry) -> None:
    watcher = GameWatcher(optimization_delay_seconds=0)
    watcher._optimization_sessions[steam_game.id] = {
        "session_changes": [{"pid": 99, "process": "game.exe", "affinity_changed": True, "affinity_before": [0, 1]}]
    }
    calls: list[list[dict]] = []

    def fake_rollback(changes):
        calls.append(changes)
        return {"success": True, "applied": [], "failed": [], "skipped": []}

    monkeypatch.setattr("app.watcher.rollback_session", fake_rollback)

    watcher.stop()

    assert calls == [[{"pid": 99, "process": "game.exe", "affinity_changed": True, "affinity_before": [0, 1]}]]
    assert watcher._optimization_sessions == {}
    assert watcher.last_optimization_action["reason"] == "watcher_stop"
