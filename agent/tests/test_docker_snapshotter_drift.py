from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agent.config import settings
from agent.main import (
    _classify_docker_snapshotter_mode,
    _log_docker_snapshotter_mode_at_startup,
    _parse_driver_status,
)


def test_parse_driver_status_pairs() -> None:
    status = _parse_driver_status([["driver-type", "io.containerd.snapshotter.v1"], ["foo", "bar"]])
    assert status == {"driver-type": "io.containerd.snapshotter.v1", "foo": "bar"}


def test_classify_docker_snapshotter_mode() -> None:
    assert _classify_docker_snapshotter_mode("overlayfs", "io.containerd.snapshotter.v1") == "containerd"
    assert _classify_docker_snapshotter_mode("overlay2", None) == "legacy"
    assert _classify_docker_snapshotter_mode("overlay2", "custom-snapshotter") == "unknown"


@pytest.mark.asyncio
async def test_startup_snapshotter_check_warns_on_mode_drift(monkeypatch, caplog) -> None:
    mock_client = MagicMock()
    mock_client.info.return_value = {
        "Driver": "overlayfs",
        "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
    }

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("agent.main.get_docker_client", lambda: mock_client)
    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(settings, "enable_docker", True)
    monkeypatch.setattr(settings, "docker_snapshotter_expected_mode", "legacy")

    with caplog.at_level("WARNING"):
        await _log_docker_snapshotter_mode_at_startup()

    assert "snapshotter drift detected" in caplog.text.lower()


@pytest.mark.asyncio
async def test_startup_snapshotter_check_does_not_warn_with_any_expected(monkeypatch, caplog) -> None:
    mock_client = MagicMock()
    mock_client.info.return_value = {
        "Driver": "overlayfs",
        "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]],
    }

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("agent.main.get_docker_client", lambda: mock_client)
    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(settings, "enable_docker", True)
    monkeypatch.setattr(settings, "docker_snapshotter_expected_mode", "any")

    with caplog.at_level("WARNING"):
        await _log_docker_snapshotter_mode_at_startup()

    assert "snapshotter drift detected" not in caplog.text.lower()
