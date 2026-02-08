"""Cleanup system foundation.

Provides:
- CleanupResult: Standardized result dataclass for all cleanup operations
- CleanupRunner: Executes cleanup tasks with unified error handling, timing, and logging
- Shared valid-ID query utilities to eliminate duplicated queries
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Standardized result from any cleanup operation."""

    task_name: str
    deleted: int = 0
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "deleted": self.deleted,
            "errors": self.errors,
            "details": self.details,
            "duration_ms": round(self.duration_ms, 1),
            "success": self.success,
        }


class CleanupRunner:
    """Executes cleanup tasks with unified error handling, timing, and logging."""

    async def run_task(
        self,
        task: Callable[..., Awaitable[CleanupResult]],
        *args: Any,
        **kwargs: Any,
    ) -> CleanupResult:
        """Run a single cleanup task with timing and error handling."""
        task_name = getattr(task, "__name__", str(task))
        start = time.monotonic()
        try:
            result = await task(*args, **kwargs)
        except Exception as e:
            logger.error(f"Cleanup task {task_name} failed: {e}")
            result = CleanupResult(task_name=task_name, errors=[str(e)])
        result.duration_ms = (time.monotonic() - start) * 1000
        if result.deleted > 0 or result.errors:
            logger.info(
                f"Cleanup {result.task_name}: deleted={result.deleted}, "
                f"errors={len(result.errors)}, duration={result.duration_ms:.0f}ms"
            )
        return result

    async def run_tasks(
        self,
        tasks: list[Callable[..., Awaitable[CleanupResult]]],
    ) -> list[CleanupResult]:
        """Run multiple cleanup tasks sequentially with unified error handling."""
        results = []
        for task in tasks:
            result = await self.run_task(task)
            results.append(result)
        return results


# ---------------------------------------------------------------------------
# Shared valid-ID query utilities
# ---------------------------------------------------------------------------

def get_valid_lab_ids(session: Session) -> set[str]:
    """Get all valid lab IDs from the database. Single query, returns set of strings."""
    return {str(id_) for (id_,) in session.query(models.Lab.id).all()}


def get_valid_host_ids(session: Session) -> set[int]:
    """Get all valid host IDs from the database."""
    return {id_ for (id_,) in session.query(models.Host.id).all()}


def get_valid_user_ids(session: Session) -> set[int]:
    """Get all valid user IDs from the database."""
    return {id_ for (id_,) in session.query(models.User.id).all()}
