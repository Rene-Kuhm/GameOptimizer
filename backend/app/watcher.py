from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psutil

from .models import GameEntry, WatcherEvent
from .optimizer import apply_profile


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class ActiveGame:
    game: GameEntry
    pid: int
    process_name: str


class GameWatcher:
    def __init__(self, poll_interval_seconds: int = 3) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._games: list[GameEntry] = []
        self._active_by_id: dict[str, ActiveGame] = {}
        self.last_detected: dict[str, Any] | None = None
        self.last_event: dict[str, Any] | None = None
        self.running = False

    def set_games(self, games: list[GameEntry]) -> None:
        self._games = games

    def _candidate_tokens(self, game: GameEntry) -> set[str]:
        tokens = {_normalize_name(game.name)}
        for exe in game.executable_names:
            stem = exe.removesuffix(".exe")
            tokens.add(_normalize_name(stem))
            tokens.add(_normalize_name(exe))
        return {t for t in tokens if len(t) >= 3}

    def _matches(self, process_name: str, game: GameEntry) -> bool:
        normalized_proc = _normalize_name(process_name)
        if not normalized_proc:
            return False

        for token in self._candidate_tokens(game):
            if normalized_proc == token or token in normalized_proc:
                return True
        return False

    def _scan(self) -> dict[str, ActiveGame]:
        found: dict[str, ActiveGame] = {}
        if not self._games:
            return found

        try:
            processes = list(psutil.process_iter(["pid", "name"]))
        except Exception:
            return found

        for proc in processes:
            proc_name = (proc.info.get("name") or "").lower()
            if not proc_name:
                continue

            for game in self._games:
                if game.id in found:
                    continue
                if self._matches(proc_name, game):
                    found[game.id] = ActiveGame(game=game, pid=proc.pid, process_name=proc_name)

        return found

    async def run(self, event_callback, default_profile: str = "default") -> None:
        self.running = True
        while self.running:
            scanned = self._scan()
            started = [item for gid, item in scanned.items() if gid not in self._active_by_id]
            stopped = [item for gid, item in self._active_by_id.items() if gid not in scanned]

            for game in started:
                optimize_result = apply_profile(game.process_name, profile=default_profile)
                event = WatcherEvent(
                    event="game_started",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                    details={"optimization": optimize_result},
                )
                self.last_detected = {
                    "game_id": game.game.id,
                    "game_name": game.game.name,
                    "process_name": game.process_name,
                    "pid": game.pid,
                }
                self.last_event = {
                    "type": event.event,
                    "timestamp": event.timestamp,
                    "game": game.game.name,
                }
                await event_callback(event)

            for game in stopped:
                event = WatcherEvent(
                    event="game_stopped",
                    game_id=game.game.id,
                    game_name=game.game.name,
                    process_name=game.process_name,
                    pid=game.pid,
                    timestamp=_now_iso(),
                )
                self.last_event = {
                    "type": event.event,
                    "timestamp": event.timestamp,
                    "game": game.game.name,
                }
                await event_callback(event)

            self._active_by_id = scanned
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self.running = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "active_count": len(self._active_by_id),
            "last_detected": self.last_detected,
            "last_event": self.last_event,
        }
