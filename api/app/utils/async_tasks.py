"""Utilities for safe async task execution.

This module provides utilities for running async tasks with proper
exception handling to prevent silent failures and improve debuggability.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import traceback
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def safe_create_task(
    coro: Awaitable[T],
    *,
    name: str | None = None,
    suppress_exceptions: bool = True,
) -> asyncio.Task[T]:
    """Create an asyncio task with proper exception handling.

    This wrapper ensures that:
    1. All exceptions are logged with full stack traces
    2. The task name is included in error messages for debugging
    3. Exceptions don't silently disappear

    Args:
        coro: The coroutine to run as a task
        name: Optional name for the task (used in error messages)
        suppress_exceptions: If True, log but don't re-raise exceptions.
                           If False, the exception will propagate when the task is awaited.

    Returns:
        The created asyncio Task

    Example:
        # Instead of:
        asyncio.create_task(run_job(job_id))

        # Use:
        safe_create_task(run_job(job_id), name=f"job:{job_id}")
    """
    task = asyncio.create_task(coro, name=name)

    def handle_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            logger.debug(f"Task '{name or task.get_name()}' was cancelled")
            return

        exc = task.exception()
        if exc is not None:
            task_name = name or task.get_name()
            # Format the exception with full traceback
            tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
            tb_str = "".join(tb_lines)

            logger.error(
                f"Background task '{task_name}' failed with exception:\n"
                f"Exception type: {type(exc).__name__}\n"
                f"Exception message: {exc}\n"
                f"Full traceback:\n{tb_str}"
            )

            if not suppress_exceptions:
                # Re-raise by letting the task's exception propagate
                # This happens automatically when task.result() is called
                pass

    task.add_done_callback(handle_exception)
    return task


def setup_asyncio_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Set up a global exception handler for the asyncio event loop.

    This catches exceptions that would otherwise be silently swallowed,
    such as exceptions in callbacks or tasks that are never awaited.

    Call this during application startup.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

    def handle_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exception = context.get("exception")
        message = context.get("message", "Unknown error")

        if exception:
            tb_lines = traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
            tb_str = "".join(tb_lines)

            logger.error(
                f"Unhandled exception in asyncio event loop:\n"
                f"Message: {message}\n"
                f"Exception type: {type(exception).__name__}\n"
                f"Exception: {exception}\n"
                f"Full traceback:\n{tb_str}"
            )
        else:
            logger.error(f"Unhandled error in asyncio event loop: {message}")

        # Also log any additional context that might be helpful
        extra_context = {k: v for k, v in context.items()
                        if k not in ("exception", "message")}
        if extra_context:
            logger.error(f"Additional context: {extra_context}")

    loop.set_exception_handler(handle_exception)
    logger.info("Asyncio exception handler configured")


class TaskRegistry:
    """Registry for tracking background tasks.

    Useful for graceful shutdown and debugging task issues.
    """

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def register(self, task: asyncio.Task, name: str) -> None:
        """Register a task for tracking."""
        async with self._lock:
            self._tasks[name] = task

        # Auto-remove when done
        def cleanup(t: asyncio.Task) -> None:
            asyncio.create_task(self._remove(name))

        task.add_done_callback(cleanup)

    async def _remove(self, name: str) -> None:
        async with self._lock:
            self._tasks.pop(name, None)

    async def cancel_all(self, timeout: float = 5.0) -> None:
        """Cancel all registered tasks and wait for them to complete."""
        async with self._lock:
            tasks = list(self._tasks.values())

        for task in tasks:
            task.cancel()

        if tasks:
            done, pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED
            )
            if pending:
                logger.warning(
                    f"{len(pending)} tasks did not complete within {timeout}s timeout"
                )

    def get_running_tasks(self) -> list[str]:
        """Get names of currently running tasks."""
        return [name for name, task in self._tasks.items() if not task.done()]


# Global task registry
task_registry = TaskRegistry()
