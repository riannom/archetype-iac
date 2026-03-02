from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.routers.console as console_mod


REAL_ASYNCIO_WAIT = asyncio.wait


class ScriptedWebSocket:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self._messages = list(messages or [])
        self.sent_texts: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False
        self.close_codes: list[int] = []

    async def receive(self) -> dict:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.sent_texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_codes.append(code)


class FakeSSHConsole:
    def __init__(
        self,
        *,
        start_ok: bool = True,
        read_chunks: list[bytes | None] | None = None,
        read_delay: float = 0.0,
    ) -> None:
        self.start_ok = start_ok
        self.read_chunks = list(read_chunks or [])
        self.read_delay = read_delay
        self.is_running = True
        self.resize_calls: list[tuple[int, int]] = []
        self.writes: list[bytes] = []
        self.closed = False

    async def start(self) -> bool:
        return self.start_ok

    async def resize(self, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))

    async def read(self) -> bytes | None:
        if self.read_chunks:
            chunk = self.read_chunks.pop(0)
            if self.read_delay:
                await asyncio.sleep(self.read_delay)
            if chunk is None:
                self.is_running = False
            return chunk
        self.is_running = False
        return None

    async def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def close(self) -> None:
        self.closed = True
        self.is_running = False


class FakeDockerConsole:
    def __init__(self, *, start_ok: bool = True, fd: int | None = None) -> None:
        self.start_ok = start_ok
        self.fd = fd
        self.is_running = True
        self.resize_calls: list[tuple[int, int]] = []
        self.writes: list[bytes] = []
        self.closed = False
        self.shell: str | None = None

    async def start_async(self, shell: str) -> bool:
        self.shell = shell
        return self.start_ok

    def resize(self, rows: int, cols: int) -> None:
        self.resize_calls.append((rows, cols))

    def get_socket_fileno(self) -> int | None:
        return self.fd

    def read_nonblocking(self) -> bytes | None:
        return None

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def close(self) -> None:
        self.closed = True
        self.is_running = False


@pytest.fixture(autouse=True)
def _clear_tcp_console_locks() -> None:
    console_mod._tcp_console_locks.clear()


async def _wait_all(tasks, return_when=None):
    return await REAL_ASYNCIO_WAIT(tasks, return_when=asyncio.ALL_COMPLETED)


@pytest.mark.asyncio
async def test_console_websocket_ssh_falls_back_without_ip():
    ws = ScriptedWebSocket()

    with patch.object(console_mod, "_get_container_boot_logs", new=AsyncMock(return_value="boot\nok")):
        with patch.object(console_mod, "_get_container_ip", new=AsyncMock(return_value=None)):
            ok = await console_mod._console_websocket_ssh(ws, "c1", "node1", "u", "p")

    assert ok is False
    assert any("Boot Log" in t for t in ws.sent_texts)
    assert any("falling back" in t for t in ws.sent_texts)


@pytest.mark.asyncio
async def test_console_websocket_ssh_falls_back_when_start_fails():
    ws = ScriptedWebSocket()
    fake_console = FakeSSHConsole(start_ok=False)

    with patch.object(console_mod, "_get_container_boot_logs", new=AsyncMock(return_value=None)):
        with patch.object(console_mod, "_get_container_ip", new=AsyncMock(return_value="10.0.0.10")):
            with patch.object(console_mod, "SSHConsole", return_value=fake_console):
                with patch.object(console_mod.asyncio, "wait", side_effect=_wait_all):
                    ok = await console_mod._console_websocket_ssh(ws, "c1", "node1", "u", "p")

    assert ok is False
    assert any("falling back" in t for t in ws.sent_texts)


@pytest.mark.asyncio
async def test_console_websocket_ssh_streaming_handles_resize():
    ws = ScriptedWebSocket(
        [
            {"type": "websocket.receive", "text": json.dumps({"type": "resize", "rows": 40, "cols": 100})},
            {"type": "websocket.receive", "text": "show version\n"},
            {"type": "websocket.receive", "bytes": b"\n"},
            {"type": "websocket.disconnect"},
        ]
    )
    fake_console = FakeSSHConsole(
        start_ok=True,
        read_chunks=[b"router#", None],
        read_delay=0.01,
    )

    with patch.object(console_mod, "_get_container_boot_logs", new=AsyncMock(return_value=None)):
        with patch.object(console_mod, "_get_container_ip", new=AsyncMock(return_value="10.0.0.10")):
            with patch.object(console_mod, "SSHConsole", return_value=fake_console):
                with patch.object(console_mod.asyncio, "wait", side_effect=_wait_all):
                    ok = await console_mod._console_websocket_ssh(ws, "c1", "node1", "u", "p")

    assert ok is True
    assert (24, 80) in fake_console.resize_calls
    assert (40, 100) in fake_console.resize_calls
    assert ws.closed


@pytest.mark.asyncio
async def test_console_websocket_docker_start_failure_sends_error():
    ws = ScriptedWebSocket()
    fake_console = FakeDockerConsole(start_ok=False)

    with patch.object(console_mod, "_get_container_boot_logs", new=AsyncMock(return_value=None)):
        with patch.object(console_mod, "DockerConsole", return_value=fake_console):
            await console_mod._console_websocket_docker(ws, "c1", "node1", "/bin/sh")

    assert any("Could not connect" in t for t in ws.sent_texts)
    assert 1011 in ws.close_codes


@pytest.mark.asyncio
async def test_console_websocket_docker_streaming_handles_resize_and_writes():
    ws = ScriptedWebSocket(
        [
            {"type": "websocket.receive", "text": json.dumps({"type": "resize", "rows": 32, "cols": 120})},
            {"type": "websocket.receive", "text": "show ip int br\n"},
            {"type": "websocket.receive", "bytes": b"\n"},
            {"type": "websocket.disconnect"},
        ]
    )
    fake_console = FakeDockerConsole(start_ok=True, fd=None)

    with patch.object(console_mod, "DockerConsole", return_value=fake_console):
        with patch.object(console_mod, "_get_container_boot_logs", new=AsyncMock(return_value="line1\nline2")):
            with patch.object(console_mod.asyncio, "wait", side_effect=_wait_all):
                await console_mod._console_websocket_docker(ws, "c1", "node1", "/bin/bash")

    assert (24, 80) in fake_console.resize_calls
    assert (32, 120) in fake_console.resize_calls
    assert b"show ip int br\n" in fake_console.writes
    assert b"\n" in fake_console.writes
    assert any("Boot Log" in t for t in ws.sent_texts)
    assert fake_console.closed
    assert ws.closed


@pytest.mark.asyncio
async def test_reset_tcp_chardev_kills_stale_process_and_cycles_backend():
    calls: list[list[str]] = []

    async def run_inline(fn):
        fn()

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["ss", "-tnp"]:
            return SimpleNamespace(
                stdout='ESTAB 0 0 127.0.0.1:7001 users:(("python3",pid=4321,fd=3))'
            )
        if cmd[:3] == ["virsh", "-c", "qemu:///system"] and "info chardev" in cmd:
            return SimpleNamespace(stdout="charserial0: socket <-> 127.0.0.1:9999")
        return SimpleNamespace(stdout="")

    with patch.object(console_mod.asyncio, "to_thread", side_effect=run_inline):
        with patch.object(console_mod.subprocess, "run", side_effect=fake_run):
            with patch("time.sleep", return_value=None):
                await console_mod._reset_tcp_chardev("lab1-r1", 7001)

    assert any(cmd[:2] == ["kill", "4321"] for cmd in calls)
    assert any("chardev-change charserial0 null" in " ".join(cmd) for cmd in calls)
    assert any("socket,host=127.0.0.1,port=7001" in " ".join(cmd) for cmd in calls)


@pytest.mark.asyncio
async def test_console_websocket_libvirt_provider_unavailable():
    ws = ScriptedWebSocket()

    with patch.object(console_mod, "get_provider", return_value=None):
        await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert any("provider not available" in t.lower() for t in ws.sent_texts)
    assert ws.close_codes[-1] == 1011


@pytest.mark.asyncio
async def test_console_websocket_libvirt_vm_not_found():
    ws = ScriptedWebSocket()
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=None)

    with patch.object(console_mod, "get_provider", return_value=provider):
        await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert any("not found" in t.lower() for t in ws.sent_texts)
    assert ws.close_codes[-1] == 1011


@pytest.mark.asyncio
async def test_console_websocket_libvirt_lock_timeout():
    ws = ScriptedWebSocket()
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["virsh", "-c", "qemu:///system", "console", "lab1-r1"]
    )

    class TimeoutLock:
        def __enter__(self):
            raise TimeoutError()

        def __exit__(self, *_args):
            return False

    with patch.object(console_mod, "get_provider", return_value=provider):
        with patch("agent.virsh_console_lock.console_lock", return_value=TimeoutLock()):
            await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert any("another session" in t.lower() for t in ws.sent_texts)
    assert ws.close_codes[-1] == 1011


@pytest.mark.asyncio
async def test_console_websocket_libvirt_tcp_lock_timeout():
    ws = ScriptedWebSocket()
    provider = MagicMock()
    provider.get_console_command = AsyncMock(
        return_value=["python3", "-c", "print('ok')", "65001"]
    )

    async def _timeout_wait_for(coro, timeout):
        if hasattr(coro, "close"):
            coro.close()
        raise asyncio.TimeoutError

    with patch.object(console_mod, "get_provider", return_value=provider):
        with patch.object(console_mod.asyncio, "wait_for", side_effect=_timeout_wait_for):
            await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert any("another session" in t.lower() for t in ws.sent_texts)
    assert ws.close_codes[-1] == 1011


@pytest.mark.asyncio
async def test_console_websocket_libvirt_process_exits_immediately():
    ws = ScriptedWebSocket()
    provider = MagicMock()
    provider.get_console_command = AsyncMock(return_value=["echo", "hello"])
    fake_process = SimpleNamespace(returncode=2)
    read_fd, write_fd = os.pipe()

    with patch.object(console_mod, "get_provider", return_value=provider):
        with patch("pty.openpty", return_value=(read_fd, write_fd)):
            with patch.object(console_mod.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=fake_process)):
                with patch.object(console_mod.asyncio, "sleep", new=AsyncMock(return_value=None)):
                    with patch("os.read", return_value=b"process failed"):
                        await console_mod._console_websocket_libvirt(ws, "lab1", "r1")

    assert any("exited unexpectedly" in t.lower() for t in ws.sent_texts)
    assert 1011 in ws.close_codes
