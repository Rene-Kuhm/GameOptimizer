from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .discovery import discover_all_games
from .models import WatcherEvent
from .optimizer import apply_profile
from .system import get_hardware_summary, get_system_metrics
from .watcher import GameWatcher


class OptimizeRequest(BaseModel):
    process_name: str = Field(min_length=1, max_length=260)
    profile: str = Field(default="default", pattern="^(default|safe)$")


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
        self.watcher = GameWatcher(poll_interval_seconds=3)
        self.games = []
        self.metrics_task: asyncio.Task | None = None
        self.watcher_task: asyncio.Task | None = None

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
            await self.ws.broadcast(
                {
                    "type": "metrics",
                    "payload": get_system_metrics(),
                    "watcher": self.watcher.status(),
                }
            )
            await asyncio.sleep(2)


state = AppState()
app = FastAPI(title="Game Optimizer Backend", version="0.1.0")


@app.on_event("startup")
async def on_startup() -> None:
    await state.refresh_games()
    state.metrics_task = asyncio.create_task(state.metrics_loop())
    state.watcher_task = asyncio.create_task(state.watcher.run(state.emit_event))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    state.watcher.stop()
    for task in [state.metrics_task, state.watcher_task]:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "watcher": state.watcher.status(),
        "games_count": len(state.games),
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
    result = apply_profile(body.process_name, body.profile)
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
