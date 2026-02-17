"""Image transfer cleanup utilities for orphaned temp files.

This module provides periodic cleanup of temporary files left behind by
failed or interrupted image transfers. Follows the same pattern as
NetworkCleanupManager in agent/network/cleanup.py.

Cleanup targets:
- /tmp/tmp*.tar: Docker image tarballs from receive_image / pull_from_controller
- {workspace}/*.part-*: Partial file-based image downloads
"""

from __future__ import annotations

import asyncio
import glob
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Files older than this (seconds) are considered stale and safe to delete
STALE_THRESHOLD_SECONDS = 1800  # 30 minutes


@dataclass
class ImageCleanupStats:
    """Statistics from a cleanup run."""

    temp_files_deleted: int = 0
    partial_files_deleted: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)


class ImageTransferCleanupManager:
    """Manages periodic cleanup of orphaned image transfer temp files.

    Usage:
        manager = ImageTransferCleanupManager(workspace_path="/var/lib/archetype-agent")
        stats = await manager.cleanup_stale_temp_files()
        await manager.start_periodic_cleanup(interval_seconds=300)
        await manager.stop_periodic_cleanup()
    """

    def __init__(self, workspace_path: str):
        self._workspace_path = workspace_path
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    async def cleanup_stale_temp_files(self) -> ImageCleanupStats:
        """Find and delete stale image transfer temp files.

        Identifies orphaned files by age (older than STALE_THRESHOLD_SECONDS).
        """
        stats = ImageCleanupStats()
        now = time.time()

        # Pattern 1: Docker image tarballs in /tmp
        for path_str in glob.glob("/tmp/tmp*.tar"):
            try:
                path = Path(path_str)
                age = now - path.stat().st_mtime
                if age > STALE_THRESHOLD_SECONDS:
                    size = path.stat().st_size
                    path.unlink(missing_ok=True)
                    stats.temp_files_deleted += 1
                    stats.bytes_freed += size
                    logger.info(
                        f"Deleted stale temp tarball: {path_str} "
                        f"(age={int(age)}s, size={size})"
                    )
            except OSError as e:
                stats.errors.append(f"Failed to clean {path_str}: {e}")

        # Pattern 2: Partial file-based downloads in workspace
        workspace = Path(self._workspace_path)
        if workspace.exists():
            for path in workspace.rglob("*.part-*"):
                try:
                    age = now - path.stat().st_mtime
                    if age > STALE_THRESHOLD_SECONDS:
                        size = path.stat().st_size
                        path.unlink(missing_ok=True)
                        stats.partial_files_deleted += 1
                        stats.bytes_freed += size
                        logger.info(
                            f"Deleted stale partial file: {path} "
                            f"(age={int(age)}s, size={size})"
                        )
                except OSError as e:
                    stats.errors.append(f"Failed to clean {path}: {e}")

        if stats.temp_files_deleted > 0 or stats.partial_files_deleted > 0:
            logger.info(
                f"Image cleanup: temp_tars={stats.temp_files_deleted}, "
                f"partials={stats.partial_files_deleted}, "
                f"freed={stats.bytes_freed} bytes"
            )

        return stats

    async def _periodic_cleanup_loop(self, interval_seconds: int) -> None:
        """Background loop for periodic cleanup."""
        logger.info(
            f"Starting periodic image cleanup (interval: {interval_seconds}s)"
        )

        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                if self._running:
                    await self.cleanup_stale_temp_files()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error during periodic image cleanup: {e}")

        logger.info("Periodic image cleanup stopped")

    async def start_periodic_cleanup(self, interval_seconds: int = 300) -> None:
        """Start periodic cleanup task."""
        if self._running:
            logger.warning("Periodic image cleanup already running")
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._periodic_cleanup_loop(interval_seconds)
        )

    async def stop_periodic_cleanup(self) -> None:
        """Stop periodic cleanup task."""
        if not self._running:
            return

        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None


# Module-level singleton
_image_cleanup_manager: ImageTransferCleanupManager | None = None


def get_image_cleanup_manager(
    workspace_path: str | None = None,
) -> ImageTransferCleanupManager:
    """Get the global ImageTransferCleanupManager instance."""
    global _image_cleanup_manager
    if _image_cleanup_manager is None:
        if workspace_path is None:
            raise ValueError("workspace_path required for first initialization")
        _image_cleanup_manager = ImageTransferCleanupManager(workspace_path)
    return _image_cleanup_manager
