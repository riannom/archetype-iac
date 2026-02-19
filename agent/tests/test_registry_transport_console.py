from __future__ import annotations

import signal
import types

import pytest

from agent import registry as registry_mod
from agent.network import transport as transport_mod
from agent import virsh_console_lock as console_lock_mod
from agent import console_session_registry as session_registry


def test_lazy_singleton_get_and_reset():
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        return object()

    singleton = registry_mod.LazySingleton(factory)
    first = singleton.get()
    second = singleton.get()

    assert first is second
    assert calls["count"] == 1

    singleton.reset()
    third = singleton.get()
    assert third is not first
    assert calls["count"] == 2


def test_transport_data_plane_ip_overrides(monkeypatch):
    from agent.config import settings

    transport_mod.set_data_plane_ip("10.0.0.5")
    monkeypatch.setattr(settings, "local_ip", "192.0.2.1", raising=False)
    assert transport_mod.get_vxlan_local_ip() == "10.0.0.5"

    transport_mod.set_data_plane_ip(None)
    assert transport_mod.get_vxlan_local_ip() == "192.0.2.1"

    transport_mod.set_data_plane_ip(None)


def test_transport_auto_detect_fallback(monkeypatch):
    from agent.config import settings

    transport_mod.set_data_plane_ip(None)
    monkeypatch.setattr(settings, "local_ip", "", raising=False)
    monkeypatch.setattr(transport_mod, "_detect_local_ip", lambda: "198.51.100.2")
    assert transport_mod.get_vxlan_local_ip() == "198.51.100.2"


def test_detect_local_ip_socket_failure(monkeypatch):
    class FakeSocket:
        def connect(self, *_):
            raise OSError("no route")

        def getsockname(self):
            return ("203.0.113.9", 0)

        def close(self):
            return None

    monkeypatch.setattr(transport_mod.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    assert transport_mod._detect_local_ip() == "127.0.0.1"


def test_detect_local_ip_success(monkeypatch):
    class FakeSocket:
        def connect(self, *_):
            return None

        def getsockname(self):
            return ("203.0.113.9", 0)

        def close(self):
            return None

    monkeypatch.setattr(transport_mod.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    assert transport_mod._detect_local_ip() == "203.0.113.9"


def test_console_lock_kills_orphans_and_acquires(monkeypatch):
    calls = {"killed": False}

    monkeypatch.setattr(console_lock_mod, "kill_orphaned_virsh", lambda _name: calls.update({"killed": True}))

    with console_lock_mod.console_lock("lab1", timeout=0.1, kill_orphans=True):
        assert calls["killed"] is True


def test_try_console_lock_returns_acquired():
    with console_lock_mod.try_console_lock("lab2") as acquired:
        assert acquired is True


def test_try_console_lock_when_busy():
    lock = console_lock_mod._get_lock("lab3")
    lock.acquire()
    try:
        with console_lock_mod.try_console_lock("lab3") as acquired:
            assert acquired is False
    finally:
        lock.release()


def test_try_console_lock_when_extraction_active():
    with console_lock_mod.extraction_session("lab-extract"):
        with console_lock_mod.try_console_lock("lab-extract") as acquired:
            assert acquired is False


def test_kill_orphaned_virsh(monkeypatch):
    class Result:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    sent_signals: list[tuple[int, int]] = []
    alive = {123}

    monkeypatch.setattr(console_lock_mod.subprocess, "run", lambda *_args, **_kwargs: Result("123\n456\n"))
    monkeypatch.setattr(console_lock_mod.os, "getpid", lambda: 456)
    monkeypatch.setattr(console_lock_mod.time, "sleep", lambda *_args, **_kwargs: None)

    now = {"value": 0.0}

    def fake_monotonic() -> float:
        now["value"] += 0.6
        return now["value"]

    monkeypatch.setattr(console_lock_mod.time, "monotonic", fake_monotonic)

    def fake_kill(pid: int, sig: int):
        sent_signals.append((pid, sig))
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError()
            return
        if sig == signal.SIGKILL:
            alive.discard(pid)

    monkeypatch.setattr(console_lock_mod.os, "kill", fake_kill)

    assert console_lock_mod.kill_orphaned_virsh("vm1") == 1
    assert (123, signal.SIGTERM) in sent_signals
    assert (123, signal.SIGKILL) in sent_signals


def test_session_registry_register_get_unregister():
    session = session_registry.ActiveConsoleSession(
        domain_name="lab1",
        master_fd=1,
        loop=None,
        websocket=None,
    )

    session_registry.register_session("lab1", session)
    assert session_registry.get_session("lab1") is session

    session_registry.unregister_session("lab1")
    assert session_registry.get_session("lab1") is None


def test_session_registry_replays_persisted_console_control(monkeypatch):
    import asyncio
    import json

    class FakeWebSocket:
        def __init__(self):
            self.sent_text = []

        async def send_text(self, text: str):
            self.sent_text.append(text)

    loop = asyncio.new_event_loop()

    def run_coroutine_threadsafe(coro, loop):
        loop.run_until_complete(coro)
        return types.SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(session_registry.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)

    changed = session_registry.set_console_control_state(
        "lab-replay",
        state="read_only",
        message="Config in progress",
    )
    assert changed is True

    ws = FakeWebSocket()
    session = session_registry.ActiveConsoleSession(
        domain_name="lab-replay",
        master_fd=1,
        loop=loop,
        websocket=ws,
    )

    try:
        session_registry.register_session("lab-replay", session)
        controls = [json.loads(msg) for msg in ws.sent_text]
        assert controls[-1]["type"] == "console-control"
        assert controls[-1]["state"] == "read_only"
        assert controls[-1]["message"] == "Config in progress"
    finally:
        session_registry.unregister_session("lab-replay")
        session_registry.set_console_control_state(
            "lab-replay",
            state="interactive",
            message="Config complete",
        )
        loop.close()


def test_clean_config_strips_noise():
    raw = "show running-config\r\n\x1b[31mBuilding configuration...\x1b[0m\n\nline1\nline2\n\n"
    cleaned = session_registry._clean_config(raw, "show running-config")
    assert cleaned == "line1\nline2"


def test_piggyback_extract_no_session():
    assert session_registry.piggyback_extract("missing") is None


def test_piggyback_extract_lock_busy(monkeypatch):
    class BusyLock:
        def acquire(self, timeout=None):
            return False

        def release(self):
            return None

    session = session_registry.ActiveConsoleSession(
        domain_name="lab2",
        master_fd=1,
        loop=None,
        websocket=None,
    )
    session._lock = BusyLock()
    session_registry.register_session("lab2", session)

    try:
        assert session_registry.piggyback_extract("lab2") is None
    finally:
        session_registry.unregister_session("lab2")


def test_piggyback_extract_success(monkeypatch):
    import asyncio

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send_bytes(self, data: bytes):
            self.sent.append(data)

    class FakeInjector:
        def __init__(self, fd, ws_forward=None, default_timeout=None):
            self.ws_forward = ws_forward
            self.calls = []
            self.last_match = "router# "
            self._expects = [
                "router# ",
                "terminal length 0\r\n",
                "show running-config\r\nline1\r\nline2\r\n",
            ]

        def send(self, text):
            self.calls.append(("send", text))

        def sendline(self, text):
            self.calls.append(("sendline", text))

        def drain(self, duration=0.5):
            self.calls.append(("drain", duration))
            return b""

        def expect(self, pattern, timeout=None):
            if "assword" in str(pattern):
                raise TimeoutError("no password prompt")
            self.last_match = "router# "
            return self._expects.pop(0)

    loop = asyncio.new_event_loop()

    def run_coroutine_threadsafe(coro, loop):
        loop.run_until_complete(coro)
        return types.SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(session_registry, "PtyInjector", FakeInjector)
    monkeypatch.setattr(session_registry.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)
    import time

    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    session = session_registry.ActiveConsoleSession(
        domain_name="lab4",
        master_fd=1,
        loop=loop,
        websocket=FakeWebSocket(),
    )
    session_registry.register_session("lab4", session)
    try:
        result = session_registry.piggyback_extract("lab4", command="show running-config")
        assert result is not None
        assert result.success is True
        assert result.config == "line1\nline2"
        assert session.input_paused.is_set()
        assert session.pty_read_paused.is_set()
    finally:
        session_registry.unregister_session("lab4")
        loop.close()


def test_piggyback_extract_user_exec_mode_rejected(monkeypatch):
    import asyncio

    class FakeWebSocket:
        async def send_bytes(self, data: bytes):
            return None

    class FakeInjector:
        def __init__(self, fd, ws_forward=None, default_timeout=None):
            self.ws_forward = ws_forward
            self.calls = []
            self.last_match = ""
            self._expects = [
                "router> ",
                "router> ",
                "terminal length 0\r\n",
                "show running-config\r\n% Invalid input detected at '^' marker.\r\n",
            ]

        def send(self, text):
            self.calls.append(("send", text))

        def sendline(self, text):
            self.calls.append(("sendline", text))

        def drain(self, duration=0.5):
            self.calls.append(("drain", duration))
            return b""

        def expect(self, pattern, timeout=None):
            if "assword" in str(pattern):
                raise TimeoutError("no password prompt")
            text = self._expects.pop(0)
            self.last_match = "router> "
            return text

    loop = asyncio.new_event_loop()

    def run_coroutine_threadsafe(coro, loop):
        loop.run_until_complete(coro)
        return types.SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(session_registry, "PtyInjector", FakeInjector)
    monkeypatch.setattr(session_registry.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)
    import time

    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    session = session_registry.ActiveConsoleSession(
        domain_name="lab5",
        master_fd=1,
        loop=loop,
        websocket=FakeWebSocket(),
    )
    session_registry.register_session("lab5", session)
    try:
        result = session_registry.piggyback_extract("lab5", command="show running-config")
        assert result is not None
        assert result.success is False
        assert "user EXEC mode" in result.error
    finally:
        session_registry.unregister_session("lab5")
        loop.close()


def test_piggyback_run_commands_no_session():
    assert session_registry.piggyback_run_commands("missing", ["show version"]) is None


def test_piggyback_run_commands_success_sets_control_mode(monkeypatch):
    import asyncio
    import json

    class FakeWebSocket:
        def __init__(self):
            self.sent_bytes = []
            self.sent_text = []

        async def send_bytes(self, data: bytes):
            self.sent_bytes.append(data)

        async def send_text(self, text: str):
            self.sent_text.append(text)

    class FakeInjector:
        def __init__(self, fd, ws_forward=None, default_timeout=None):
            self.ws_forward = ws_forward
            self.calls = []
            self.last_match = "router# "
            self._expects = [
                "router# ",
                "show version\r\nok\r\n",
                "show clock\r\nok\r\n",
            ]

        def send(self, text):
            self.calls.append(("send", text))

        def sendline(self, text):
            self.calls.append(("sendline", text))

        def expect(self, pattern, timeout=None):
            return self._expects.pop(0)

    loop = asyncio.new_event_loop()

    def run_coroutine_threadsafe(coro, loop):
        loop.run_until_complete(coro)
        return types.SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(session_registry, "PtyInjector", FakeInjector)
    monkeypatch.setattr(session_registry.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)
    import time

    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    ws = FakeWebSocket()
    session = session_registry.ActiveConsoleSession(
        domain_name="lab6",
        master_fd=1,
        loop=loop,
        websocket=ws,
    )
    session_registry.register_session("lab6", session)
    try:
        result = session_registry.piggyback_run_commands(
            "lab6",
            ["show version", "show clock"],
        )
        assert result is not None
        assert result.success is True
        assert result.commands_run == 2
        assert session.input_paused.is_set()
        assert session.pty_read_paused.is_set()

        controls = [json.loads(msg) for msg in ws.sent_text]
        assert controls[0]["type"] == "console-control"
        assert controls[0]["state"] == "read_only"
        assert controls[-1]["state"] == "interactive"
    finally:
        session_registry.unregister_session("lab6")
        loop.close()


def test_console_lock_timeout(monkeypatch):
    lock = console_lock_mod._get_lock("lab-timeout")
    lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            with console_lock_mod.console_lock("lab-timeout", timeout=0.01, kill_orphans=False):
                pass
    finally:
        lock.release()
