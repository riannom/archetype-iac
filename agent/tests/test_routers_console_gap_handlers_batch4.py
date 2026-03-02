from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import agent.routers.console as console_mod
from agent.config import settings


REAL_ASYNCIO_WAIT = asyncio.wait
REAL_ASYNCIO_SLEEP = asyncio.sleep


class WS:
    def __init__(self, script: list[dict | Exception] | None = None):
        self.script = list(script or [])
        self.sent_texts: list[str] = []
        self.sent_bytes: list[bytes] = []

    async def receive(self):
        if not self.script:
            return {"type": "websocket.disconnect"}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, text: str):
        self.sent_texts.append(text)

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def close(self, code: int = 1000):
        return None


class DockerConsole:
    def __init__(self, fd: int):
        self.fd = fd
        self.is_running = True
        self._reads: list[bytes | None] = [None]

    async def start_async(self, shell: str):
        return True

    def resize(self, rows: int, cols: int):
        return None

    def get_socket_fileno(self):
        return self.fd

    def read_nonblocking(self):
        value = self._reads.pop(0) if self._reads else None
        if value is None:
            self.is_running = False
        return value

    def write(self, _data: bytes):
        return None

    def close(self):
        self.is_running = False


class LoopStub:
    def __init__(self, *, add_raises: bool = False, remove_raises: bool = False):
        self.add_raises = add_raises
        self.remove_raises = remove_raises

    def add_reader(self, _fd, callback):
        if self.add_raises:
            raise RuntimeError("add_reader failed")
        callback()

    def remove_reader(self, _fd):
        if self.remove_raises:
            raise RuntimeError("remove_reader failed")


class Proc:
    def __init__(self, returncode: int | None = None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _awaitable(value):
    async def _inner():
        return value

    return _inner()


async def _wait_all(tasks, return_when=None):
    return await REAL_ASYNCIO_WAIT(tasks, return_when=asyncio.ALL_COMPLETED)


def _registry_module(pty_states: list[bool], input_states: list[bool]):
    class _SeqEvent:
        def __init__(self, states: list[bool]):
            self.states = list(states)
            self.idx = 0

        def is_set(self):
            if self.idx < len(self.states):
                value = self.states[self.idx]
                self.idx += 1
                return value
            return True

    class _Lock:
        def acquire(self, timeout=0):
            return True

        def release(self):
            return None

    class ActiveConsoleSession:
        def __init__(self, **_kwargs):
            self.pty_read_paused = _SeqEvent(pty_states)
            self.input_paused = _SeqEvent(input_states)
            self._lock = _Lock()

    return SimpleNamespace(
        ActiveConsoleSession=ActiveConsoleSession,
        register_session=lambda *_a, **_k: None,
        unregister_session=lambda *_a, **_k: None,
    )


@pytest.fixture(autouse=True)
def _clear_locks():
    console_mod._tcp_console_locks.clear()


@pytest.mark.asyncio
async def test_console_websocket_docker_none_read_and_write_timeout(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    docker_console = DockerConsole(fd=31)
    loop = LoopStub()

    async def _fake_wait_for(awaitable, timeout):
        if timeout == settings.console_input_timeout:
            if hasattr(awaitable, "close"):
                awaitable.close()
            docker_console.is_running = False
            raise asyncio.TimeoutError()
        return await awaitable

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "DockerConsole", lambda *_a, **_k: docker_console)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)

    await console_mod._console_websocket_docker(ws, "c1", "n1", "/bin/sh")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ws_exc",
    [
        WebSocketDisconnect(code=1000),
        RuntimeError("receive error"),
    ],
)
async def test_console_websocket_libvirt_read_websocket_exception_branches(monkeypatch, ws_exc):
    ws = WS([ws_exc])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "ok"])
    proc = Proc(returncode=None)
    loop = LoopStub()

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_a, **_k: b"")

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")


@pytest.mark.asyncio
async def test_console_websocket_libvirt_pause_gates_and_write_branches(monkeypatch):
    ws = WS(
        [
            {"type": "websocket.receive", "text": "a"},
            {"type": "websocket.receive", "text": "b"},
            {"type": "websocket.disconnect"},
        ]
    )
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-r1"]
    )
    proc = Proc(returncode=None)
    loop = LoopStub()

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    event_wait_calls = {"n": 0}
    queue_get_calls = {"n": 0}

    async def _fake_wait_for(awaitable, timeout):
        owner = None
        frame = getattr(awaitable, "cr_frame", None)
        if frame is not None:
            owner = frame.f_locals.get("self")

        if isinstance(owner, asyncio.Event):
            event_wait_calls["n"] += 1
            if hasattr(awaitable, "close"):
                awaitable.close()
            if event_wait_calls["n"] == 1:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0)
            return None

        if isinstance(owner, asyncio.Queue):
            queue_get_calls["n"] += 1
            if queue_get_calls["n"] == 1:
                if hasattr(awaitable, "close"):
                    awaitable.close()
                raise asyncio.TimeoutError()
            return await awaitable

        return await awaitable

    read_calls = {"n": 0}

    def _os_read(_fd, _n):
        read_calls["n"] += 1
        if proc.returncode is not None:
            return b""
        if read_calls["n"] > 1000:
            proc.returncode = 0
            return b""
        return b"vm#"

    write_calls = {"n": 0}

    def _os_write(_fd, _data):
        write_calls["n"] += 1
        proc.returncode = 0
        raise RuntimeError("write fail")

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", lambda *_a, **_k: REAL_ASYNCIO_SLEEP(0))
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())
    monkeypatch.setitem(
        sys.modules,
        "agent.console_session_registry",
        _registry_module(
            pty_states=[False, True, True, False, True, True],
            input_states=[False, True, True],
        ),
    )
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", _os_read)
    monkeypatch.setattr("os.write", _os_write)

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")
    assert write_calls["n"] >= 1


@pytest.mark.asyncio
async def test_console_websocket_libvirt_read_pty_exception_and_remove_reader_error(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "ok"])
    proc = Proc(returncode=None)
    loop = LoopStub(add_raises=True, remove_raises=True)

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")


@pytest.mark.asyncio
async def test_console_websocket_libvirt_read_pty_oserror_continue(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "ok"])
    proc = Proc(returncode=None)
    loop = LoopStub()
    read_calls = {"n": 0}

    def _os_read(_fd, _n):
        read_calls["n"] += 1
        proc.returncode = 0
        raise OSError("pty read fail")

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", _os_read)

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")
    assert read_calls["n"] >= 1
