from __future__ import annotations

import asyncio
import logging

import pytest

import app.utils.async_tasks as async_tasks


@pytest.mark.asyncio
async def test_safe_create_task_logs_exception(caplog) -> None:
    async def boom():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR):
        task = async_tasks.safe_create_task(boom(), name="boom-task")

        with pytest.raises(ValueError):
            await task

        # Yield to the event loop so the done_callback (scheduled via
        # call_soon) has a chance to execute and log the error.
        await asyncio.sleep(0)

    assert any("boom-task" in record.message for record in caplog.records)


def test_setup_asyncio_exception_handler_logs(caplog) -> None:
    captured = {}

    class FakeLoop:
        def set_exception_handler(self, handler):
            captured["handler"] = handler

    loop = FakeLoop()
    async_tasks.setup_asyncio_exception_handler(loop=loop)

    handler = captured["handler"]
    with caplog.at_level(logging.ERROR):
        handler(loop, {"message": "oops"})

    assert any("oops" in record.message for record in caplog.records)


def test_setup_asyncio_exception_handler_with_exception(caplog) -> None:
    captured = {}

    class FakeLoop:
        def set_exception_handler(self, handler):
            captured["handler"] = handler

    loop = FakeLoop()
    async_tasks.setup_asyncio_exception_handler(loop=loop)

    try:
        raise RuntimeError("bad")
    except RuntimeError as exc:
        handler = captured["handler"]
        with caplog.at_level(logging.ERROR):
            handler(loop, {"message": "fail", "exception": exc})

    assert any("RuntimeError" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_task_registry_register_and_cleanup() -> None:
    registry = async_tasks.TaskRegistry()

    async def work():
        await asyncio.sleep(0.01)

    task = asyncio.create_task(work())
    await registry.register(task, "task-1")

    assert "task-1" in registry.get_running_tasks()

    await task
    await asyncio.sleep(0)

    assert "task-1" not in registry.get_running_tasks()


@pytest.mark.asyncio
async def test_task_registry_cancel_all() -> None:
    registry = async_tasks.TaskRegistry()

    async def work():
        await asyncio.sleep(1)

    task = asyncio.create_task(work())
    await registry.register(task, "task-2")

    await registry.cancel_all(timeout=0.01)

    assert task.cancelled() or task.done()
