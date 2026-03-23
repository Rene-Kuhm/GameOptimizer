from __future__ import annotations

import logging
import os

_LOGGING_READY = False


def setup_logging() -> None:
    global _LOGGING_READY
    if _LOGGING_READY:
        return

    level_name = os.environ.get("GAME_OPTIMIZER_LOG_LEVEL", "INFO").upper().strip() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [game-optimizer:%(name)s] %(message)s",
    )
    _LOGGING_READY = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
