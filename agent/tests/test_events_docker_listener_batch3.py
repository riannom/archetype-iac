from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.events.base import NodeEvent, NodeEventType


class DockerException(Exception):
    pass


def _install_fake_docker(monkeypatch):
    errors_mod = types.ModuleType("docker.errors")
    errors_mod.DockerException = DockerException

    docker_mod = types.ModuleType("docker")
    docker_mod.errors = errors_mod
    docker_mod.DockerClient = object
    docker_mod.from_env = lambda: object()

    monkeypatch.setitem(sys.modules, "docker", docker_mod)
    monkeypatch.setitem(sys.modules, "docker.errors", errors_mod)


def _load_docker_events(monkeypatch):
    _install_fake_docker(monkeypatch)
    import agent.events.docker_events as docker_events

    importlib.reload(docker_events)
    return docker_events


@pytest.mark.asyncio
async def test_start_handles_docker_error_then_cancel(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()

    monkeypatch.setattr(docker_events.asyncio, "to_thread", AsyncMock(return_value=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(docker_events.asyncio, "sleep", AsyncMock(return_value=None))

    calls = {"n": 0}

    async def _listen(_callback):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DockerException("daemon disconnected")
        raise asyncio.CancelledError()

    listener._listen_loop = _listen
    await listener.start(AsyncMock(return_value=None))

    assert calls["n"] == 2
    assert listener._running is False


@pytest.mark.asyncio
async def test_start_handles_unexpected_error_path(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()

    monkeypatch.setattr(docker_events.asyncio, "to_thread", AsyncMock(return_value=SimpleNamespace(close=lambda: None)))

    async def _sleep_and_stop(_delay):
        listener._running = False

    monkeypatch.setattr(docker_events.asyncio, "sleep", _sleep_and_stop)

    async def _listen(_callback):
        raise RuntimeError("unexpected listener failure")

    listener._listen_loop = _listen
    await listener.start(AsyncMock(return_value=None))

    assert listener._running is False


def test_event_reader_thread_paths(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()

    queue_items: list[object] = []
    listener._async_queue = SimpleNamespace(put_nowait=lambda item: queue_items.append(item))

    class _Loop:
        @staticmethod
        def call_soon_threadsafe(fn, *args):
            fn(*args)

    loop = _Loop()

    listener._thread_stop.clear()
    listener._event_reader_thread([{"id": 1}, {"id": 2}], loop)
    assert queue_items[-1] is None

    queue_items.clear()

    def _bad_iter():
        yield {"id": 1}
        raise RuntimeError("stream boom")

    listener._thread_stop.clear()
    listener._event_reader_thread(_bad_iter(), loop)
    assert any(isinstance(item, RuntimeError) for item in queue_items)
    assert queue_items[-1] is None

    queue_items.clear()
    listener._thread_stop.set()
    listener._event_reader_thread([{"id": 3}], loop)
    assert queue_items == [None]


class _FakeEvents:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self._alive = False

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self._alive = False


@pytest.mark.asyncio
async def test_listen_loop_processes_timeout_event_callback_error_and_cleanup(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()
    listener._running = True
    listener._stop_event = asyncio.Event()

    fake_events = _FakeEvents()
    listener._client = SimpleNamespace(events=lambda **kwargs: fake_events)

    monkeypatch.setattr(docker_events.threading, "Thread", _FakeThread)

    node_event = NodeEvent(
        lab_id="lab-a",
        node_name="n1",
        container_id="cid",
        event_type=NodeEventType.STARTED,
        timestamp=datetime.now(),
        status="running",
    )
    listener._parse_event = lambda _event: node_event

    sequence = iter([
        asyncio.TimeoutError(),
        {"Type": "container", "Action": "start"},
        None,
    ])

    async def _wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        item = next(sequence)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(docker_events.asyncio, "wait_for", _wait_for)

    callback = AsyncMock(side_effect=RuntimeError("callback boom"))
    await listener._listen_loop(callback)

    assert fake_events.closed is True
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_listen_loop_raises_exception_events(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()
    listener._running = True
    listener._stop_event = asyncio.Event()

    fake_events = _FakeEvents()
    listener._client = SimpleNamespace(events=lambda **kwargs: fake_events)
    monkeypatch.setattr(docker_events.threading, "Thread", _FakeThread)

    async def _wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        return RuntimeError("queue failure")

    monkeypatch.setattr(docker_events.asyncio, "wait_for", _wait_for)

    with pytest.raises(RuntimeError, match="queue failure"):
        await listener._listen_loop(AsyncMock(return_value=None))

    assert fake_events.closed is True


@pytest.mark.asyncio
async def test_stop_and_is_running_paths(monkeypatch):
    docker_events = _load_docker_events(monkeypatch)
    listener = docker_events.DockerEventListener()

    listener._running = True
    listener._stop_event = asyncio.Event()

    class _JoinThread:
        def __init__(self):
            self.joined = False

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.joined = True

    thread = _JoinThread()
    listener._reader_thread = thread

    class _Client:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True
            raise RuntimeError("close failed")

    listener._client = _Client()

    assert listener.is_running() is True
    await listener.stop()
    assert listener.is_running() is False
    assert thread.joined is True
    assert listener._client is None
