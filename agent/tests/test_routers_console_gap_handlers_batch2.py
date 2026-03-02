from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

import agent.routers.console as console_mod


REAL_ASYNCIO_WAIT = asyncio.wait


class ScriptedWebSocket:
    def __init__(self, script: list[dict | Exception], *, delay: float = 0.0) -> None:
        self.script = list(script)
        self.delay = delay
        self.sent_texts: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.close_codes: list[int] = []

    async def receive(self) -> dict:
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.script:
            return {"type": "websocket.disconnect"}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_codes.append(code)


class FakeLoop:
    def __init__(self):
        self.added: list[int] = []
        self.removed: list[int] = []

    def add_reader(self, fd: int, callback):
        self.added.append(fd)
        callback()

    def remove_reader(self, fd: int):
        self.removed.append(fd)


class FakeSSHConsole:
    def __init__(self, *, read_error: bool = False) -> None:
        self.is_running = True
        self.read_error = read_error
        self.writes: list[bytes] = []
        self.resize_calls: list[tuple[int, int]] = []

    async def start(self) -> bool:
        return True

    async def resize(self, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))

    async def read(self) -> bytes | None:
        if self.read_error:
            raise RuntimeError("read failed")
        self.is_running = False
        return None

    async def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def close(self) -> None:
        self.is_running = False


class FakeDockerConsole:
    def __init__(self, *, fd: int | None, write_error: bool = False) -> None:
        self.fd = fd
        self.write_error = write_error
        self.is_running = True
        self.resize_calls: list[tuple[int, int]] = []
        self._reads = [b"router#", None]

    async def start_async(self, shell: str) -> bool:
        return True

    def resize(self, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))

    def get_socket_fileno(self) -> int | None:
        return self.fd

    def read_nonblocking(self) -> bytes | None:
        if not self._reads:
            self.is_running = False
            return None
        data = self._reads.pop(0)
        if data is None:
            self.is_running = False
        return data

    def write(self, _data: bytes) -> None:
        if self.write_error:
            raise RuntimeError("write failed")

    def close(self) -> None:
        self.is_running = False


class FakeProcess:
    def __init__(self, *, returncode: int | None = None, wait_timeout: bool = False):
        self.returncode = returncode
        self.wait_timeout = wait_timeout
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        if self.wait_timeout:
            raise asyncio.TimeoutError()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _fake_registry_module(lock_acquire_ok: bool, calls: list[str]):
    class _Lock:
        def acquire(self, timeout=0):
            return lock_acquire_ok

        def release(self):
            calls.append("lock_release")

    class ActiveConsoleSession:
        def __init__(self, **_kwargs):
            self.pty_read_paused = SimpleNamespace(is_set=lambda: True)
            self.input_paused = SimpleNamespace(is_set=lambda: True)
            self._lock = _Lock()

    def register_session(_domain, _session):
        calls.append("register")

    def unregister_session(_domain):
        calls.append("unregister")

    return SimpleNamespace(
        ActiveConsoleSession=ActiveConsoleSession,
        register_session=register_session,
        unregister_session=unregister_session,
    )


async def _wait_all(tasks, return_when=None):
    return await REAL_ASYNCIO_WAIT(tasks, return_when=asyncio.ALL_COMPLETED)


@pytest.fixture(autouse=True)
def _clear_tcp_locks():
    console_mod._tcp_console_locks.clear()


@pytest.mark.asyncio
async def test_console_websocket_ssh_json_decode_and_receive_error_paths():
    ws = ScriptedWebSocket(
        [
            {"type": "websocket.receive", "text": "{"},
            RuntimeError("receive failed"),
        ]
    )
    fake_console = FakeSSHConsole(read_error=True)

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
        mp.setattr(console_mod, "_get_container_ip", AsyncMock(return_value="192.0.2.5"))
        mp.setattr(console_mod, "SSHConsole", lambda *_args, **_kwargs: fake_console)
        mp.setattr(console_mod.asyncio, "wait", _wait_all)
        ok = await console_mod._console_websocket_ssh(ws, "c1", "r1", "u", "p")

    assert ok is True
    assert (24, 80) in fake_console.resize_calls


@pytest.mark.asyncio
async def test_console_websocket_docker_read_loop_and_write_error_paths(monkeypatch):
    ws = ScriptedWebSocket(
        [
            {"type": "websocket.receive", "text": "{"},
            RuntimeError("ws error"),
        ]
    )
    fake_console = FakeDockerConsole(fd=123, write_error=True)
    fake_loop = FakeLoop()

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "DockerConsole", lambda *_args, **_kwargs: fake_console)
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: fake_loop)

    await console_mod._console_websocket_docker(ws, "c1", "r1", "/bin/sh")

    assert ws.sent_bytes == [b"router#"]
    assert fake_loop.added == [123]
    assert fake_loop.removed == [123]


@pytest.mark.asyncio
async def test_reset_tcp_chardev_handles_kill_and_monitor_exceptions(monkeypatch):
    async def _inline(func):
        return func()

    def _run(cmd, **_kwargs):
        if cmd[:2] == ["ss", "-tnp"]:
            return SimpleNamespace(stdout='ESTAB users:(("python3",pid=5000,fd=3))')
        if cmd[:2] == ["kill", "5000"]:
            raise RuntimeError("kill failed")
        raise RuntimeError("monitor failed")

    monkeypatch.setattr(console_mod.asyncio, "to_thread", _inline)
    monkeypatch.setattr(console_mod.subprocess, "run", _run)

    await console_mod._reset_tcp_chardev("arch-lab1-r1", 65001)


@pytest.mark.asyncio
async def test_console_websocket_libvirt_virsh_streaming_and_cleanup(monkeypatch):
    ws = ScriptedWebSocket(
        [
            {"type": "websocket.receive", "text": json.dumps({"type": "resize", "rows": 33, "cols": 120})},
            {"type": "websocket.receive", "text": "{"},
            {"type": "websocket.receive", "text": "show version\n"},
            {"type": "websocket.receive", "bytes": b"\n"},
            {"type": "websocket.disconnect"},
        ],
        delay=0.001,
    )
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-r1"]
    )
    process = FakeProcess(returncode=None)
    fake_loop = FakeLoop()
    registry_calls: list[str] = []

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            registry_calls.append("lock_exit")
            return False

    def _os_read(_fd, _n):
        process.returncode = 5
        return b"vm#"

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(console_mod.asyncio, "get_event_loop", lambda: fake_loop)
    monkeypatch.setattr(console_mod, "_reset_tcp_chardev", AsyncMock(return_value=None))
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("fcntl.ioctl", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("os.read", _os_read)
    monkeypatch.setattr("os.write", lambda _fd, data: len(data))
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", _fake_registry_module(True, registry_calls))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())

    await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert "register" in registry_calls
    assert "unregister" in registry_calls
    assert "lock_exit" in registry_calls
    assert any("exited with code 5" in text for text in ws.sent_texts)


@pytest.mark.asyncio
async def test_console_websocket_libvirt_unreg_when_lock_acquire_times_out(monkeypatch):
    ws = ScriptedWebSocket([])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "arch-lab1-r1"]
    )
    process = FakeProcess(returncode=2)
    registry_calls: list[str] = []

    class _LockCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_args, **_kwargs: b"console failed")
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", _fake_registry_module(False, registry_calls))
    monkeypatch.setattr("agent.virsh_console_lock.console_lock", lambda *_a, **_k: _LockCtx())

    await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert "register" in registry_calls
    assert "unregister" in registry_calls


@pytest.mark.asyncio
async def test_console_websocket_libvirt_tcp_telnet_reset_and_release_lock(monkeypatch):
    ws = ScriptedWebSocket([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["python3", "-c", "print('tcp')", "65001"])
    process = FakeProcess(returncode=1)
    reset_calls: list[tuple[str, int]] = []

    async def _reset(domain_name: str, tcp_port: int):
        reset_calls.append((domain_name, tcp_port))

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(console_mod, "_reset_tcp_chardev", _reset)
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_args, **_kwargs: b"")

    await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert reset_calls == [("arch-lab1-r1", 65001)]
    assert console_mod._tcp_console_locks[65001].locked() is False


@pytest.mark.asyncio
async def test_console_websocket_libvirt_process_wait_timeout_cleanup(monkeypatch):
    ws = ScriptedWebSocket([{"type": "websocket.disconnect"}])
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "ok"])
    process = FakeProcess(returncode=None, wait_timeout=True)

    monkeypatch.setattr(console_mod, "get_provider", lambda name: provider if name == "libvirt" else None)
    monkeypatch.setattr(console_mod.asyncio, "to_thread", lambda fn, *a, **k: _awaitable(fn(*a, **k)))
    monkeypatch.setattr(console_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr("pty.openpty", lambda: os.pipe())
    monkeypatch.setattr("os.read", lambda *_args, **_kwargs: b"")

    await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert process.terminated is True
    assert process.killed is True


@pytest.mark.asyncio
async def test_console_websocket_ssh_websocket_disconnect_branch(monkeypatch):
    ws = ScriptedWebSocket([WebSocketDisconnect(code=1000)])
    fake_console = FakeSSHConsole(read_error=False)

    monkeypatch.setattr(console_mod, "_get_container_boot_logs", AsyncMock(return_value=None))
    monkeypatch.setattr(console_mod, "_get_container_ip", AsyncMock(return_value="192.0.2.7"))
    monkeypatch.setattr(console_mod, "SSHConsole", lambda *_args, **_kwargs: fake_console)
    monkeypatch.setattr(console_mod.asyncio, "wait", _wait_all)

    ok = await console_mod._console_websocket_ssh(ws, "c1", "r1", "u", "p")
    assert ok is True


def _awaitable(value):
    async def _inner():
        return value

    return _inner()
