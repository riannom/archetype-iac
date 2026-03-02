from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent.routers.console as console_mod
from agent.config import settings


REAL_ASYNCIO_WAIT = asyncio.wait


class WS:
    def __init__(self, script: list[dict | Exception] | None = None):
        self.script = list(script or [])
        self.accepted = False
        self.sent_texts: list[str] = []
        self.closed_codes: list[int] = []

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self.script:
            await asyncio.sleep(0.01)
            return {"type": "websocket.disconnect"}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, text: str):
        self.sent_texts.append(text)

    async def send_bytes(self, _data: bytes):
        return None

    async def close(self, code: int = 1000):
        self.closed_codes.append(code)


class DockerConsole:
    def __init__(self, _container_name: str):
        self.is_running = True
        self.fd = 33

    async def start_async(self, shell: str):
        return True

    def resize(self, rows: int, cols: int):
        return None

    def get_socket_fileno(self):
        return self.fd

    def read_nonblocking(self):
        return None

    def write(self, _data: bytes):
        return None

    def close(self):
        self.is_running = False


class LoopStub:
    def add_reader(self, _fd, callback):
        callback()

    def remove_reader(self, _fd):
        return None


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


@pytest.mark.asyncio
async def test_console_websocket_docker_config_exception_sends_failure(monkeypatch):
    ws = WS()
    docker_provider = SimpleNamespace(get_container_name=lambda _lab, _node: "ctr")

    def _provider(name: str):
        if name == "docker":
            return docker_provider
        return None

    monkeypatch.setattr(console_mod, "get_provider", _provider)
    monkeypatch.setattr(console_mod, "_check_container_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(console_mod, "_get_console_config", AsyncMock(side_effect=RuntimeError("bad config")))

    await console_mod.console_websocket(ws, "lab1", "n1")

    assert ws.accepted is True
    assert any("Console connection failed" in text for text in ws.sent_texts)
    assert 1011 in ws.closed_codes


@pytest.mark.asyncio
async def test_console_websocket_falls_back_to_libvirt_when_docker_missing(monkeypatch):
    ws = WS()
    docker_provider = SimpleNamespace(get_container_name=lambda _lab, _node: "ctr")
    libvirt_provider = SimpleNamespace()

    def _provider(name: str):
        if name == "docker":
            return docker_provider
        if name == "libvirt":
            return libvirt_provider
        return None

    monkeypatch.setattr(console_mod, "get_provider", _provider)
    monkeypatch.setattr(console_mod, "_check_container_exists", AsyncMock(return_value=False))
    libvirt_ws = AsyncMock(return_value=None)
    monkeypatch.setattr(console_mod, "_console_websocket_libvirt", libvirt_ws)

    await console_mod.console_websocket(ws, "lab1", "n1")

    assert ws.accepted is True
    libvirt_ws.assert_awaited_once_with(ws, "lab1", "n1")


@pytest.mark.asyncio
async def test_console_websocket_docker_write_timeout_branch(monkeypatch):
    ws = WS([])
    console = DockerConsole("ctr")
    loop = LoopStub()

    async def _fake_wait_for(awaitable, timeout):
        if timeout == settings.console_input_timeout:
            if hasattr(awaitable, "close"):
                awaitable.close()
            console.is_running = False
            raise asyncio.TimeoutError()
        return await awaitable

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "DockerConsole", lambda _name: console)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)

    await console_mod._console_websocket_docker(ws, "ctr", "n1", "/bin/sh")


@pytest.mark.asyncio
async def test_reset_tcp_chardev_ss_exception_branch(monkeypatch):
    async def _sync(fn):
        return fn()

    monkeypatch.setattr(console_mod.asyncio, "to_thread", _sync)
    monkeypatch.setattr(
        console_mod.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ss failed")),
    )

    await console_mod._reset_tcp_chardev("arch-lab1-n1", 65000)


@pytest.mark.asyncio
async def test_console_websocket_libvirt_read_pty_timeout_and_pause_recheck(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-n1"]
    )
    proc = Proc(returncode=None)
    loop = LoopStub()

    class _Lock:
        def acquire(self, timeout=0):
            return True

        def release(self):
            return None

    class _SeqEvent:
        def __init__(self, states: list[bool]):
            self.states = list(states)
            self.i = 0

        def is_set(self):
            if self.i < len(self.states):
                value = self.states[self.i]
                self.i += 1
                return value
            return True

    class ActiveConsoleSession:
        def __init__(self, **_kwargs):
            # 693 pre-check: True (not paused)
            # 706 re-check: False -> line 707 continue
            self.pty_read_paused = _SeqEvent([True, False, True])
            self.input_paused = _SeqEvent([True, True])
            self._lock = _Lock()

    registry_mod = SimpleNamespace(
        ActiveConsoleSession=ActiveConsoleSession,
        register_session=lambda *_a, **_k: None,
        unregister_session=lambda *_a, **_k: None,
    )

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    event_wait_calls = {"n": 0}

    async def _fake_wait_for(awaitable, timeout):
        frame = getattr(awaitable, "cr_frame", None)
        owner = frame.f_locals.get("self") if frame is not None else None
        if isinstance(owner, asyncio.Event):
            event_wait_calls["n"] += 1
            if hasattr(awaitable, "close"):
                awaitable.close()
            if event_wait_calls["n"] == 1:
                raise asyncio.TimeoutError()  # line 701
            proc.returncode = 0
            return None  # second wait reaches line 707
        return await awaitable

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", registry_mod)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_a, **_k: b"")

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")


@pytest.mark.asyncio
async def test_console_websocket_libvirt_pause_recheck_continue_branch(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-n1"]
    )
    proc = Proc(returncode=None)
    loop = LoopStub()

    class _Lock:
        def acquire(self, timeout=0):
            return True

        def release(self):
            return None

    class _SeqEvent:
        def __init__(self, states: list[bool]):
            self.states = list(states)
            self.i = 0

        def is_set(self):
            if self.i < len(self.states):
                value = self.states[self.i]
                self.i += 1
                return value
            return True

    class ActiveConsoleSession:
        def __init__(self, **_kwargs):
            # 693 pre-check: True
            # 706 re-check: False -> line 707 continue
            self.pty_read_paused = _SeqEvent([True, False, True])
            self.input_paused = _SeqEvent([True, True])
            self._lock = _Lock()

    registry_mod = SimpleNamespace(
        ActiveConsoleSession=ActiveConsoleSession,
        register_session=lambda *_a, **_k: None,
        unregister_session=lambda *_a, **_k: None,
    )

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    event_wait_calls = {"n": 0}

    async def _fake_wait_for(awaitable, timeout):
        frame = getattr(awaitable, "cr_frame", None)
        owner = frame.f_locals.get("self") if frame is not None else None
        if isinstance(owner, asyncio.Event):
            event_wait_calls["n"] += 1
            if hasattr(awaitable, "close"):
                awaitable.close()
            if event_wait_calls["n"] == 2:
                proc.returncode = 0
            return None
        return await awaitable

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", registry_mod)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_a, **_k: b"")

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")
