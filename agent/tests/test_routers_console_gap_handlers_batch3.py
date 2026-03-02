from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import agent.routers.console as console_mod


class WS:
    def __init__(self, script: list[dict | Exception] | None = None, *, close_raises: bool = False):
        self.script = list(script or [])
        self.close_raises = close_raises
        self.sent_texts: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed_codes: list[int] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self.script:
            await asyncio.sleep(0)
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
        self.closed_codes.append(code)
        if self.close_raises:
            raise RuntimeError("close failed")


class SSHConsoleSlow:
    def __init__(self):
        self.is_running = True
        self.resize_calls: list[tuple[int, int]] = []

    async def start(self):
        return True

    async def resize(self, rows: int, cols: int):
        self.resize_calls.append((rows, cols))

    async def read(self):
        await asyncio.sleep(10)
        return b""

    async def write(self, _data: bytes):
        return None

    async def close(self):
        self.is_running = False


class DockerConsoleStub:
    def __init__(self, *, fd: int | None = None):
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
        if not self._reads:
            self.is_running = False
            return None
        val = self._reads.pop(0)
        if val is None:
            self.is_running = False
        return val

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
    def __init__(self, returncode: int | None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


@pytest.fixture(autouse=True)
def _clear_tcp_console_locks():
    console_mod._tcp_console_locks.clear()


@pytest.mark.asyncio
async def test_console_websocket_falls_back_to_docker_on_ssh_failure():
    ws = WS()
    docker_provider = SimpleNamespace(get_container_name=lambda _lab, _node: "c1")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(console_mod, "get_provider", lambda name: docker_provider if name == "docker" else None)
        mp.setattr(console_mod, "_check_container_exists", AsyncMock(return_value=True))
        mp.setattr(console_mod, "_get_console_config", AsyncMock(return_value=("ssh", "/bin/sh", "u", "p")))
        mp.setattr(console_mod, "_console_websocket_ssh", AsyncMock(return_value=False))
        docker_ws = AsyncMock()
        mp.setattr(console_mod, "_console_websocket_docker", docker_ws)
        await console_mod.console_websocket(ws, "lab1", "n1")

    assert ws.accepted is True
    assert docker_ws.await_count == 1


@pytest.mark.asyncio
async def test_console_websocket_ssh_cancel_pending_and_close_error(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}], close_raises=True)
    console = SSHConsoleSlow()

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "_get_container_ip", AsyncMock(return_value="192.0.2.10"))
    monkeypatch.setattr(console_mod, "SSHConsole", lambda *_a, **_k: console)

    ok = await console_mod._console_websocket_ssh(ws, "c1", "n1", "u", "p")
    assert ok is True


@pytest.mark.asyncio
async def test_console_websocket_ssh_write_timeout_and_exception_paths(monkeypatch):
    ws = WS([{"type": "websocket.receive", "text": "x"}])
    console = SSHConsoleSlow()
    calls = {"n": 0}

    async def _fake_wait_for(awaitable, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError()
        if calls["n"] == 2:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise RuntimeError("boom")
        return await awaitable

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "_get_container_ip", AsyncMock(return_value="192.0.2.10"))
    monkeypatch.setattr(console_mod, "SSHConsole", lambda *_a, **_k: console)
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)

    ok = await console_mod._console_websocket_ssh(ws, "c1", "n1", "u", "p")
    assert ok is True


@pytest.mark.asyncio
async def test_console_websocket_docker_timeout_disconnect_and_cleanup_paths(monkeypatch):
    ws = WS([WebSocketDisconnect(code=1000)], close_raises=True)
    console = DockerConsoleStub(fd=123)
    loop = LoopStub(remove_raises=True)

    calls = {"n": 0}

    async def _fake_wait_for(awaitable, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            # read_container timeout then write_container timeout
            if calls["n"] == 2:
                console.is_running = False
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError()
        return await awaitable

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "DockerConsole", lambda *_a, **_k: console)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(console_mod.asyncio, "wait_for", _fake_wait_for)

    await console_mod._console_websocket_docker(ws, "c1", "n1", "/bin/sh")


@pytest.mark.asyncio
async def test_console_websocket_docker_read_container_exception_branch(monkeypatch):
    ws = WS([{"type": "websocket.disconnect"}])
    console = DockerConsoleStub(fd=11)
    loop = LoopStub(add_raises=True)

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "DockerConsole", lambda *_a, **_k: console)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: loop)

    await console_mod._console_websocket_docker(ws, "c1", "n1", "/bin/sh")


@pytest.mark.asyncio
async def test_console_websocket_libvirt_immediate_exit_error_read_exception(monkeypatch):
    ws = WS()
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "x"])
    proc = Proc(returncode=1)

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_a, **_k: (_ for _ in ()).throw(OSError("read fail")))

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")
    assert any("exited unexpectedly" in t.lower() for t in ws.sent_texts)


@pytest.mark.asyncio
async def test_console_websocket_libvirt_wait_exception_and_finally_error_paths(monkeypatch):
    ws = WS(close_raises=True)
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-r1"])
    proc = Proc(returncode=None)

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            raise RuntimeError("lock exit fail")

    async def _raise_wait(*_args, **_kwargs):
        raise RuntimeError("wait fail")

    close_calls = {"n": 0}

    def _close_fail(_fd):
        close_calls["n"] += 1
        if close_calls["n"] == 1:
            return None
        raise OSError("close fail")

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "wait", _raise_wait)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.close", _close_fail)

    await console_mod._console_websocket_libvirt(ws, "lab1", "n1")


@pytest.mark.asyncio
async def test_console_websocket_libvirt_create_subprocess_exception_finally_paths(monkeypatch):
    ws = WS(close_raises=True)
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "x"])

    def _close_fail(_fd):
        raise OSError("close fail")

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(side_effect=RuntimeError("spawn fail")))
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.close", _close_fail)

    with pytest.raises(RuntimeError, match="spawn fail"):
        await console_mod._console_websocket_libvirt(ws, "lab1", "n1")


def _awaitable(value):
    async def _inner():
        return value

    return _inner()
