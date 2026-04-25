from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .discovery import discover_all_games
from .logging_setup import get_logger, setup_logging
from .models import WatcherEvent
from .optimizer import apply_profile
from .profiles import OptimizationProfiles
from .system import get_hardware_summary, get_system_metrics
from .watcher import GameWatcher

setup_logging()
logger = get_logger(__name__)


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 300) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("invalid integer env var name=%s value=%s using_default=%s", name, raw, default)
        return default
    return max(min_value, min(max_value, value))


def _env_float(name: str, default: float, *, min_value: float = 0.0, max_value: float = 120.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("invalid float env var name=%s value=%s using_default=%s", name, raw, default)
        return default
    return max(min_value, min(max_value, value))


class OptimizeRequest(BaseModel):
    process_name: str = Field(min_length=1, max_length=260)
    profile: str = Field(default="default", pattern="^[a-zA-Z0-9_-]{2,64}$")


class WSManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for conn in self.connections:
            try:
                await conn.send_json(payload)
            except Exception:
                stale.append(conn)
        for conn in stale:
            self.disconnect(conn)


class AppState:
    def __init__(self) -> None:
        self.ws = WSManager()
        profiles_path_raw = os.environ.get("GAME_OPTIMIZER_PROFILES_PATH", "").strip()
        profiles_path = None
        if profiles_path_raw:
            profiles_path = Path(profiles_path_raw)
        self.optimization_profiles = OptimizationProfiles(config_path=profiles_path)
        self.watcher = GameWatcher(
            poll_interval_seconds=_env_int("GAME_OPTIMIZER_POLL_INTERVAL_SECONDS", default=3, min_value=1, max_value=60),
            profile_resolver=self.optimization_profiles,
            optimization_delay_seconds=_env_float(
                "GAME_OPTIMIZER_OPTIMIZATION_DELAY_SECONDS",
                default=0.0,
                min_value=0.0,
                max_value=120.0,
            ),
        )
        self.games = []
        self.metrics_task: asyncio.Task | None = None
        self.watcher_task: asyncio.Task | None = None
        self.last_metrics: dict[str, Any] | None = None

    async def refresh_games(self) -> None:
        self.games = discover_all_games()
        self.watcher.set_games(self.games)

    async def emit_event(self, event: WatcherEvent) -> None:
        await self.ws.broadcast(
            {
                "type": "watcher_event",
                "payload": asdict(event),
                "watcher": self.watcher.status(),
            }
        )

    async def metrics_loop(self) -> None:
        while True:
            metrics = get_system_metrics()
            self.last_metrics = metrics
            await self.ws.broadcast(
                {
                    "type": "metrics",
                    "payload": metrics,
                    "watcher": self.watcher.status(),
                }
            )
            await asyncio.sleep(2)


state = AppState()
app = FastAPI(title="Game Optimizer Backend", version="0.1.0")


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("backend startup")
    await state.refresh_games()
    state.last_metrics = get_system_metrics()
    state.metrics_task = asyncio.create_task(state.metrics_loop())
    state.watcher_task = asyncio.create_task(state.watcher.run(state.emit_event))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("backend shutdown")
    state.watcher.stop()
    for task in [state.metrics_task, state.watcher_task]:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@app.get("/health")
async def health() -> dict[str, Any]:
    metrics = state.last_metrics or get_system_metrics()
    watcher_status = state.watcher.status()
    return {
        "status": "ok",
        "watcher": watcher_status,
        "games_count": len(state.games),
        "diagnostics": {
            "active_telemetry_source": metrics.get("gpu_source", "unavailable"),
            "telemetry_confidence": metrics.get("gpu_confidence"),
            "telemetry_reason": metrics.get("gpu_confidence_reason"),
            "watcher_summary": {
                "running": watcher_status.get("running", False),
                "active_count": watcher_status.get("active_count", 0),
                "tracked_optimization_sessions": watcher_status.get("tracked_optimization_sessions", 0),
            },
            "last_optimization_action_status": watcher_status.get("last_optimization_action"),
        },
    }


@app.get("/system/metrics")
async def system_metrics() -> dict[str, Any]:
    return get_system_metrics()


@app.get("/hardware")
async def hardware() -> dict[str, Any]:
    return get_hardware_summary()


@app.get("/games")
async def games() -> dict[str, Any]:
    return {
        "games": [asdict(game) for game in state.games],
        "count": len(state.games),
    }


@app.post("/optimize/apply")
async def optimize_apply(body: OptimizeRequest) -> dict[str, Any]:
    profile_name, profile_config, profile_resolution = state.optimization_profiles.resolve_for_game(None, body.profile)
    result = apply_profile(body.process_name, profile_name, profile_config=profile_config)
    result["profile_resolution"] = profile_resolution
    return {"ok": True, "result": result}


@app.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket) -> None:
    await state.ws.connect(ws)
    try:
        await ws.send_json(
            {
                "type": "snapshot",
                "payload": {
                    "metrics": get_system_metrics(),
                    "hardware": get_hardware_summary(),
                    "games": [asdict(game) for game in state.games],
                    "watcher": state.watcher.status(),
                },
            }
        )
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        state.ws.disconnect(ws)
    except Exception:
        state.ws.disconnect(ws)
