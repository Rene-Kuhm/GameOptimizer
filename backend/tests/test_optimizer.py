from __future__ import annotations

import pytest

from app import optimizer


@pytest.mark.parametrize(
    ("raw", "expected", "error"),
    [
        ([0, 1, 1], [0, 1], None),
        ([], None, "cpu_affinity cannot be empty"),
        ([0, "1"], None, "cpu_affinity values must be integers"),
        (None, None, None),
    ],
)
def test_sanitize_affinity(monkeypatch: pytest.MonkeyPatch, raw, expected, error) -> None:
    monkeypatch.setattr(optimizer.psutil, "cpu_count", lambda logical=True: 4)

    affinity, affinity_error = optimizer._sanitize_affinity(raw)

    assert affinity == expected
    assert affinity_error == error


def test_rollback_skips_missing_previous_affinity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(optimizer.psutil, "Process", lambda pid: object())

    result = optimizer.rollback_session(
        [{"pid": 42, "process": "game.exe", "affinity_changed": True, "affinity_before": None}]
    )

    assert result["success"] is True
    assert result["skipped"][0]["reason"] == "missing_previous_affinity"


def test_rollback_process_not_found_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_process(pid):
        raise optimizer.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(optimizer.psutil, "Process", raise_no_process)

    result = optimizer.rollback_session(
        [{"pid": 42, "process": "game.exe", "affinity_changed": True, "affinity_before": [0, 1]}]
    )

    assert result["success"] is True
    assert result["skipped"][0]["reason"] == "process_not_found"


def test_priority_api_unavailable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(optimizer, "win32api", None)
    monkeypatch.setattr(optimizer, "win32con", None)
    monkeypatch.setattr(optimizer, "win32process", None)

    current, error = optimizer._get_process_priority(123)

    assert current is None
    assert error == "Windows priority APIs unavailable"
