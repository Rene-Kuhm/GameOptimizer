from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "app.discovery",
        "app.optimizer",
        "app.system",
        "app.watcher",
    ],
)
def test_windows_oriented_modules_import_on_current_platform(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert module is not None
