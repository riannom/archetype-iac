from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent import image_cleanup


def _write_file(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_cleanup_stale_temp_files_deletes_stale_items(monkeypatch, tmp_path: Path) -> None:
    now = 1_000_000.0
    stale_mtime = now - (image_cleanup.STALE_THRESHOLD_SECONDS + 5)
    fresh_mtime = now - 60

    stale_tar = tmp_path / "tmp-stale.tar"
    fresh_tar = tmp_path / "tmp-fresh.tar"
    stale_part = tmp_path / "workspace" / "node.img.part-123"
    fresh_part = tmp_path / "workspace" / "node.img.part-456"

    _write_file(stale_tar, size=50, mtime=stale_mtime)
    _write_file(fresh_tar, size=70, mtime=fresh_mtime)
    _write_file(stale_part, size=80, mtime=stale_mtime)
    _write_file(fresh_part, size=90, mtime=fresh_mtime)

    monkeypatch.setattr(image_cleanup.time, "time", lambda: now)
    monkeypatch.setattr(
        image_cleanup.glob,
        "glob",
        lambda _pattern: [str(stale_tar), str(fresh_tar)],
    )

    manager = image_cleanup.ImageTransferCleanupManager(str(tmp_path / "workspace"))
    stats = await manager.cleanup_stale_temp_files()

    assert stats.temp_files_deleted == 1
    assert stats.partial_files_deleted == 1
    assert stats.bytes_freed == 130
    assert stats.errors == []

    assert stale_tar.exists() is False
    assert stale_part.exists() is False
    assert fresh_tar.exists() is True
    assert fresh_part.exists() is True


@pytest.mark.asyncio
async def test_cleanup_stale_temp_files_collects_errors(monkeypatch, tmp_path: Path) -> None:
    now = 2_000_000.0
    stale_mtime = now - (image_cleanup.STALE_THRESHOLD_SECONDS + 30)
    stale_tar = tmp_path / "tmp-raise.tar"

    _write_file(stale_tar, size=10, mtime=stale_mtime)
    monkeypatch.setattr(image_cleanup.time, "time", lambda: now)
    monkeypatch.setattr(image_cleanup.glob, "glob", lambda _pattern: [str(stale_tar)])

    original_unlink = Path.unlink

    def _failing_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == stale_tar:
            raise OSError("cannot delete stale tar")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _failing_unlink)

    manager = image_cleanup.ImageTransferCleanupManager(str(tmp_path / "workspace"))
    stats = await manager.cleanup_stale_temp_files()

    assert stats.temp_files_deleted == 0
    assert stats.partial_files_deleted == 0
    assert len(stats.errors) == 1
    assert "cannot delete stale tar" in stats.errors[0]


@pytest.mark.asyncio
async def test_periodic_cleanup_start_and_stop() -> None:
    manager = image_cleanup.ImageTransferCleanupManager("/tmp/workspace")
    await manager.start_periodic_cleanup(interval_seconds=3600)
    first_task = manager._cleanup_task

    assert manager._running is True
    assert first_task is not None

    await manager.start_periodic_cleanup(interval_seconds=3600)
    assert manager._cleanup_task is first_task

    await manager.stop_periodic_cleanup()
    assert manager._running is False
    assert manager._cleanup_task is None


def test_get_image_cleanup_manager_singleton(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(image_cleanup, "_image_cleanup_manager", None)

    with pytest.raises(ValueError, match="workspace_path required"):
        image_cleanup.get_image_cleanup_manager()

    manager_one = image_cleanup.get_image_cleanup_manager(str(tmp_path / "workspace-a"))
    manager_two = image_cleanup.get_image_cleanup_manager(str(tmp_path / "workspace-b"))

    assert manager_one is manager_two
