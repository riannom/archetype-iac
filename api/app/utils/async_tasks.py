"""Utilities for safe async task execution.

This module provides utilities for running async tasks with proper
exception handling to prevent silent failures and improve debuggability.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Captured at startup so sync threadpool handlers can schedule async tasks.
_main_loop: asyncio.AbstractEventLoop | None = None


def capture_event_loop() -> None:
    """Store the running event loop. Call once during app startup."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def safe_create_task(
    coro: Awaitable[T],
    *,
    name: str | None = None,
    suppress_exceptions: bool = True,
) -> asyncio.Task[T] | None:
    """Create an asyncio task with proper exception handling.

    Works from both async and sync (threadpool) contexts.  When called from
    a sync FastAPI handler (which runs in a threadpool), the coroutine is
    scheduled onto the main event loop via ``call_soon_threadsafe``.

    Returns:
        The created asyncio Task, or None when scheduled cross-thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Called from a sync context (e.g. threadpool handler).
        if _main_loop is None or _main_loop.is_closed():
            logger.error("No event loop available for background task %s", name)
            coro.close()  # prevent "was never awaited" warning
            return None
        _main_loop.call_soon_threadsafe(
            lambda: safe_create_task(coro, name=name, suppress_exceptions=suppress_exceptions)
        )
        return None

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
