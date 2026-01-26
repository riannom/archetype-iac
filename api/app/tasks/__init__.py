"""Background task registry for lifespan management."""
from __future__ import annotations

import asyncio
from typing import Callable, Coroutine, Any

# Registry of background task functions
_task_registry: list[Callable[[], Coroutine[Any, Any, None]]] = []

# Running task handles
_running_tasks: list[asyncio.Task] = []


def register_task(task_func: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Register a background task function to be started on app startup."""
    _task_registry.append(task_func)


async def start_all_tasks() -> None:
    """Start all registered background tasks."""
    for task_func in _task_registry:
        task = asyncio.create_task(task_func())
        _running_tasks.append(task)


async def stop_all_tasks() -> None:
    """Stop all running background tasks."""
    for task in _running_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _running_tasks.clear()
