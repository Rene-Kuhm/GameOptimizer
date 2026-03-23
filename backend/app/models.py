from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GameEntry:
    id: str
    name: str
    source: str
    install_dir: str | None = None
    executable_names: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatcherEvent:
    event: str
    game_id: str
    game_name: str
    process_name: str
    pid: int
    timestamp: str
    details: dict[str, Any] = field(default_factory=dict)
