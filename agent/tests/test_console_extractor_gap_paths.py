"""Coverage gap tests for agent.console_extractor."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
from types import SimpleNamespace

import pytest


# Ensure pexpect exists so SerialConsoleExtractor branches are testable.
if importlib.util.find_spec("pexpect") is None:
    _pexpect_stub = types.ModuleType("pexpect")

    class TIMEOUT(Exception):
        pass

    class EOF(Exception):
        pass

    class _FakeSpawn:
        def __init__(self, *args, **kwargs):
            self.before = ""
            self.after = ""

    _pexpect_stub.TIMEOUT = TIMEOUT
    _pexpect_stub.EOF = EOF
    _pexpect_stub.spawn = _FakeSpawn
    sys.modules["pexpect"] = _pexpect_stub

import agent.console_extractor as console_extractor


def _make_extractor(
    *,
    domain_name: str = "vm1",
    libvirt_uri: str = "qemu:///system",
    timeout: int = 30,
    tcp_port: int | None = None,
    child=None,
):
    extractor = console_extractor.SerialConsoleExtractor.__new__(
        console_extractor.SerialConsoleExtractor
    )
    extractor.domain_name = domain_name
    extractor.libvirt_uri = libvirt_uri
    extractor.timeout = timeout
    extractor.tcp_port = tcp_port
    extractor.child = child
    return extractor


def _set_registry_module(monkeypatch, **overrides):
    module = types.ModuleType("agent.console_session_registry")
    defaults = {
        "piggyback_extract": lambda **kwargs: None,
        "piggyback_run_commands": lambda **kwargs: None,
        "piggyback_run_commands_capture": lambda **kwargs: None,
        "get_session": lambda domain_name: None,
        "set_console_control_state": lambda domain_name, *, state, message: True,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "agent.console_session_registry", module)
    return module


def _set_virsh_console_lock_module(
    monkeypatch,
    *,
    console_lock=None,
    extraction_session=None,
):
    module = types.ModuleType("agent.virsh_console_lock")
    module.console_lock = (
        console_lock if console_lock is not None else (lambda *args, **kwargs: contextlib.nullcontext())
    )
    module.extraction_session = (
        extraction_session
        if extraction_session is not None
        else (lambda *args, **kwargs: contextlib.nullcontext())
    )
    monkeypatch.setitem(sys.modules, "agent.virsh_console_lock", module)
    return module


def _set_vendors_module(monkeypatch, *, settings, vendor_config):
    module = types.ModuleType("agent.vendors")
    module.get_config_extraction_settings = lambda kind: settings
    module.get_vendor_config = lambda kind: vendor_config
    monkeypatch.setitem(sys.modules, "agent.vendors", module)
    return module


def test_init_raises_when_pexpect_is_unavailable(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", False)
    with pytest.raises(ImportError):
        console_extractor.SerialConsoleExtractor("vm1")


def test_extract_config_invalid_piggyback_falls_back_to_locked_console(monkeypatch):
    extractor = _make_extractor()
    piggyback = console_extractor.ExtractionResult(success=True, config="not a config")
    _set_registry_module(monkeypatch, piggyback_extract=lambda **kwargs: piggyback)

    def _console_lock(*args, **kwargs):
        class _Ctx:
            def __enter__(self):
                raise TimeoutError("busy")

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    _set_virsh_console_lock_module(monkeypatch, console_lock=_console_lock)
    monkeypatch.setattr(
        extractor,
        "_validate_extracted_config",
        lambda **kwargs: (False, "invalid payload"),
    )

    result = extractor.extract_config(retries=0)
    assert result.success is False
    assert "locked" in result.error.lower()


def test_extract_config_retries_and_succeeds_after_delay(monkeypatch):
    extractor = _make_extractor()
    _set_registry_module(monkeypatch, piggyback_extract=lambda **kwargs: None)
    _set_virsh_console_lock_module(monkeypatch)

    calls = {"count": 0}

    def _extract_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return console_extractor.ExtractionResult(success=False, error="try again")
        return console_extractor.ExtractionResult(success=True, config="hostname r1\ninterface e0")

    monkeypatch.setattr(extractor, "_extract_config_inner", _extract_once)
    sleeps: list[int] = []
    monkeypatch.setattr(console_extractor.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = extractor.extract_config(retries=1)
    assert result.success is True
    assert calls["count"] == 2
    assert sleeps == [2]


def test_spawn_console_tcp_path_resets_chardev_and_spawns_bridge(monkeypatch):
    extractor = _make_extractor(timeout=12, tcp_port=2101)

    reset_calls: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        console_extractor,
        "_reset_tcp_chardev_sync",
        lambda domain, port, uri: reset_calls.append((domain, port, uri)),
    )

    child = SimpleNamespace()
    spawn_calls: list[tuple[object, ...]] = []

    def _spawn(cmd, args, timeout=None, encoding=None):
        spawn_calls.append((cmd, tuple(args), timeout, encoding))
        return child

    monkeypatch.setattr(console_extractor.pexpect, "spawn", _spawn)
    sleeps: list[int] = []
    monkeypatch.setattr(console_extractor.time, "sleep", lambda seconds: sleeps.append(seconds))

    err = extractor._spawn_console()
    assert err is None
    assert extractor.child is child
    assert reset_calls == [("vm1", 2101, "qemu:///system")]
    assert spawn_calls and spawn_calls[0][0] == "python3"
    assert sleeps == [1]


def test_spawn_console_pty_timeout_returns_error(monkeypatch):
    extractor = _make_extractor(tcp_port=None)

    class _Child:
        def expect(self, *args, **kwargs):
            raise console_extractor.pexpect.TIMEOUT("connect timeout")

    monkeypatch.setattr(
        console_extractor.pexpect,
        "spawn",
        lambda *args, **kwargs: _Child(),
    )

    err = extractor._spawn_console()
    assert "Timeout waiting for console connection" in err


def test_wait_for_prompt_handles_setup_dialog(monkeypatch):
    extractor = _make_extractor(timeout=20)
    prompt_patterns = extractor._prompt_patterns(r"[>#]\s*$")
    setup_dialog_index = len(prompt_patterns) + 1

    class _Child:
        def __init__(self):
            self.calls = 0
            self.sent: list[str] = []
            self.sentline: list[str] = []
            self.before = "setup prompt"

        def expect(self, patterns, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return setup_dialog_index
            return 0

        def send(self, data):
            self.sent.append(data)

        def sendline(self, data):
            self.sentline.append(data)

    extractor.child = _Child()
    assert extractor._wait_for_prompt(r"[>#]\s*$") is True
    assert extractor.child.sentline == ["no"]
    assert extractor.child.sent == ["\r"]


def test_wait_for_prompt_timeout_when_before_access_fails(monkeypatch):
    extractor = _make_extractor(timeout=5)

    class _Child:
        def expect(self, patterns, timeout=None):
            raise console_extractor.pexpect.TIMEOUT("still booting")

        def send(self, data):
            return None

        @property
        def before(self):  # pragma: no cover - exercised via exception branch
            raise RuntimeError("buffer unavailable")

    extractor.child = _Child()
    assert extractor._wait_for_prompt(r"[>#]\s*$") is False


def test_prime_console_answers_initial_dialog_with_no():
    extractor = _make_extractor()
    prompt_patterns = extractor._prompt_patterns(r"[>#]\s*$")
    dialog_index = len(prompt_patterns) + 3

    class _Child:
        def __init__(self):
            self.sentline: list[str] = []
            self.sent: list[str] = []

        def expect(self, patterns, timeout=None):
            return dialog_index

        def sendline(self, data):
            self.sentline.append(data)

        def send(self, data):
            self.sent.append(data)

    extractor.child = _Child()
    assert extractor._prime_console_for_prompt(r"[>#]\s*$") is True
    assert extractor.child.sentline == ["no"]
    assert extractor.child.sent == ["\r"]


def test_handle_login_times_out_after_retries(monkeypatch):
    extractor = _make_extractor(timeout=1)
    extractor._prompt_patterns = lambda _prompt: [r"[>#]\s*$"]  # type: ignore[method-assign]

    class _Child:
        def __init__(self):
            self.sent: list[str] = []

        def expect(self, patterns, timeout=None):
            raise console_extractor.pexpect.TIMEOUT("no prompt")

        def send(self, data):
            self.sent.append(data)

        def sendline(self, data):
            self.sent.append(data)

    extractor.child = _Child()
    ticks = {"value": 0}

    def _fake_time():
        ticks["value"] += 1
        return ticks["value"]

    monkeypatch.setattr(console_extractor.time, "time", _fake_time)
    assert extractor._handle_login("admin", "admin", r"[>#]\s*$") is False
    assert "\r" in extractor.child.sent


def test_enter_enable_mode_password_path_waits_for_prompt(monkeypatch):
    extractor = _make_extractor()

    class _Child:
        def __init__(self):
            self.sentline: list[str] = []

        def sendline(self, data):
            self.sentline.append(data)

        def expect(self, patterns, timeout=None):
            return 0

    extractor.child = _Child()
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda pattern: True)
    assert extractor._enter_enable_mode("enable-secret", r"[>#]\s*$") is True
    assert extractor.child.sentline == ["enable", "enable-secret"]


def test_enter_enable_mode_timeout_returns_false():
    extractor = _make_extractor()

    class _Child:
        def sendline(self, data):
            return None

        def expect(self, patterns, timeout=None):
            raise console_extractor.pexpect.TIMEOUT("enable timeout")

    extractor.child = _Child()
    assert extractor._enter_enable_mode("pw", r"[>#]\s*$") is False


def test_attempt_enable_mode_uses_empty_password(monkeypatch):
    extractor = _make_extractor(timeout=3)

    class _Child:
        def __init__(self):
            self.sentline: list[str] = []

        def sendline(self, data):
            self.sentline.append(data)

        def expect(self, patterns, timeout=None):
            return 0

    extractor.child = _Child()
    waited = {"called": 0}
    monkeypatch.setattr(
        extractor,
        "_wait_for_prompt",
        lambda prompt: waited.__setitem__("called", waited["called"] + 1) or True,
    )
    extractor._attempt_enable_mode("", r"[>#]\s*$")
    assert extractor.child.sentline == ["enable", ""]
    assert waited["called"] == 1


def test_attempt_enable_mode_swallows_exceptions():
    extractor = _make_extractor()

    class _Child:
        def sendline(self, data):
            return None

        def expect(self, patterns, timeout=None):
            raise RuntimeError("bad state")

    extractor.child = _Child()
    extractor._attempt_enable_mode("", r"[>#]\s*$")


def test_disable_paging_ignores_non_fatal_errors():
    extractor = _make_extractor()

    class _Child:
        def sendline(self, data):
            return None

        def expect(self, patterns, timeout=None):
            raise RuntimeError("paging command failed")

    extractor.child = _Child()
    extractor._disable_paging("terminal length 0", r"[>#]\s*$")


def test_execute_command_decodes_byte_output():
    extractor = _make_extractor()

    class _Child:
        def __init__(self):
            self.before = b"hostname r1\r\n!"

        def sendline(self, data):
            return None

        def expect(self, patterns, timeout=None):
            return 0

    extractor.child = _Child()
    output = extractor._execute_command("show run", r"[>#]\s*$")
    assert output == "hostname r1\r\n!"


def test_execute_command_timeout_returns_none():
    extractor = _make_extractor()

    class _Child:
        def sendline(self, data):
            return None

        def expect(self, patterns, timeout=None):
            raise console_extractor.pexpect.TIMEOUT("cmd timeout")

    extractor.child = _Child()
    assert extractor._execute_command("show run", r"[>#]\s*$") is None


def test_clean_config_removes_transport_banners_and_pager():
    extractor = _make_extractor()
    raw = (
        "Connected to domain vm1\n"
        "Escape character is ^]\n"
        "switch#show running-config\n"
        "--More--\n"
        "hostname switch\n"
        "switch#\n"
    )
    cleaned = extractor._clean_config(raw, "show running-config")
    assert "Connected to domain" not in cleaned
    assert "Escape character is" not in cleaned
    assert "--More--" not in cleaned
    assert "hostname switch" in cleaned


def test_validate_extracted_config_rejects_short_non_config_output():
    extractor = _make_extractor()
    valid, reason = extractor._validate_extracted_config(
        "hello world\nthis is short",
        "show running-config",
    )
    assert valid is False
    assert "too short" in reason


def test_cleanup_force_kills_when_close_fails(monkeypatch):
    class _Child:
        def __init__(self):
            self.pid = 321
            self.sentcontrol: list[str] = []

        def sendcontrol(self, char):
            self.sentcontrol.append(char)

        def close(self, force=False):
            raise RuntimeError("stuck process")

    extractor = _make_extractor(tcp_port=None, child=_Child())
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        console_extractor.os,
        "kill",
        lambda pid, sig: killed.append((pid, sig)),
    )
    monkeypatch.setattr(console_extractor.time, "sleep", lambda _seconds: None)
    extractor._cleanup()
    assert killed == [(321, console_extractor.signal.SIGKILL)]
    assert extractor.child is None


def test_run_commands_empty_list_returns_success():
    extractor = _make_extractor()
    result = extractor.run_commands([])
    assert result.success is True
    assert result.commands_run == 0


def test_run_commands_returns_failed_piggyback_without_direct(monkeypatch):
    extractor = _make_extractor()
    piggyback = console_extractor.CommandResult(success=False, error="busy")
    _set_registry_module(monkeypatch, piggyback_run_commands=lambda **kwargs: piggyback)
    monkeypatch.setattr(
        console_extractor.SerialConsoleExtractor,
        "_run_commands_inner",
        lambda *args, **kwargs: pytest.fail("direct path must not run"),
    )
    result = extractor.run_commands(["show version"])
    assert result.success is False
    assert result.error == "busy"


def test_run_commands_retries_after_lock_timeout(monkeypatch):
    extractor = _make_extractor()
    _set_registry_module(monkeypatch, piggyback_run_commands=lambda **kwargs: None)

    calls = {"count": 0}

    def _console_lock(*args, **kwargs):
        calls["count"] += 1
        idx = calls["count"]

        class _Ctx:
            def __enter__(self):
                if idx == 1:
                    raise TimeoutError("busy")
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    _set_virsh_console_lock_module(monkeypatch, console_lock=_console_lock)
    monkeypatch.setattr(
        extractor,
        "_run_commands_inner",
        lambda *args, **kwargs: console_extractor.CommandResult(success=True, commands_run=1),
    )
    sleeps: list[int] = []
    monkeypatch.setattr(console_extractor.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = extractor.run_commands(["show version"], retries=1)
    assert result.success is True
    assert calls["count"] == 2
    assert sleeps == [2]


def test_run_commands_capture_empty_list_returns_success():
    extractor = _make_extractor()
    result = extractor.run_commands_capture([])
    assert result.success is True
    assert result.outputs == []


def test_run_commands_capture_returns_failed_piggyback(monkeypatch):
    extractor = _make_extractor()
    piggyback = console_extractor.CommandCaptureResult(success=False, error="session busy")
    _set_registry_module(
        monkeypatch,
        piggyback_run_commands_capture=lambda **kwargs: piggyback,
    )
    result = extractor.run_commands_capture(["show clock"])
    assert result.success is False
    assert result.error == "session busy"


def test_run_commands_capture_direct_path_uses_kill_orphans_false_when_session_exists(monkeypatch):
    extractor = _make_extractor()
    lock_calls: list[bool] = []

    def _console_lock(domain_name, timeout=60, kill_orphans=True):
        lock_calls.append(kill_orphans)
        return contextlib.nullcontext()

    _set_registry_module(
        monkeypatch,
        piggyback_run_commands_capture=lambda **kwargs: None,
        get_session=lambda domain_name: object(),
    )
    _set_virsh_console_lock_module(monkeypatch, console_lock=_console_lock)
    monkeypatch.setattr(
        extractor,
        "_run_commands_capture_inner",
        lambda *args, **kwargs: console_extractor.CommandCaptureResult(success=True, commands_run=1),
    )

    result = extractor.run_commands_capture(["show clock"], retries=0)
    assert result.success is True
    assert lock_calls == [False]


def test_run_commands_capture_retries_after_lock_timeout(monkeypatch):
    extractor = _make_extractor()
    _set_registry_module(
        monkeypatch,
        piggyback_run_commands_capture=lambda **kwargs: None,
        get_session=lambda domain_name: None,
    )

    calls = {"count": 0}

    def _console_lock(domain_name, timeout=60, kill_orphans=True):
        calls["count"] += 1
        idx = calls["count"]

        class _Ctx:
            def __enter__(self):
                if idx == 1:
                    raise TimeoutError("busy")
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Ctx()

    _set_virsh_console_lock_module(monkeypatch, console_lock=_console_lock)
    monkeypatch.setattr(
        extractor,
        "_run_commands_capture_inner",
        lambda *args, **kwargs: console_extractor.CommandCaptureResult(success=True, commands_run=1),
    )
    sleeps: list[int] = []
    monkeypatch.setattr(console_extractor.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = extractor.run_commands_capture(["show clock"], retries=1)
    assert result.success is True
    assert calls["count"] == 2
    assert sleeps == [2]


def test_run_commands_inner_fails_when_prompt_not_found(monkeypatch):
    extractor = _make_extractor()
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda prompt: False)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_inner(["show clock"], "", "", "", r"[>#]\s*$")
    assert result.success is False
    assert "cli prompt" in result.error.lower()


def test_run_commands_inner_fails_when_enable_mode_cannot_be_entered(monkeypatch):
    extractor = _make_extractor()
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_handle_login", lambda *args: True)
    monkeypatch.setattr(extractor, "_enter_enable_mode", lambda *args: False)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_inner(
        ["show clock"],
        "admin",
        "pw",
        "enable",
        r"[>#]\s*$",
    )
    assert result.success is False
    assert "enable mode" in result.error.lower()


def test_run_commands_inner_counts_partial_success(monkeypatch):
    class _Child:
        def __init__(self):
            self.sentline: list[str] = []

        def sendline(self, data):
            self.sentline.append(data)

    extractor = _make_extractor(child=_Child())
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_handle_login", lambda *args: True)
    monkeypatch.setattr(extractor, "_attempt_enable_mode", lambda *args: None)
    waits = iter([True, False])
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda prompt: next(waits))
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_inner(
        ["show version", "show clock"],
        "admin",
        "pw",
        "",
        r"[>#]\s*$",
    )
    assert result.success is True
    assert result.commands_run == 1
    assert extractor.child.sentline == ["show version", "show clock"]


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (console_extractor.pexpect.TIMEOUT("timeout"), "timeout"),
        (console_extractor.pexpect.EOF("closed"), "closed"),
        (RuntimeError("boom"), "boom"),
    ],
)
def test_run_commands_inner_exception_paths(monkeypatch, exc, needle):
    extractor = _make_extractor()

    def _raise():
        raise exc

    monkeypatch.setattr(extractor, "_spawn_console", _raise)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)
    result = extractor._run_commands_inner(["show clock"], "", "", "", r"[>#]\s*$")
    assert result.success is False
    assert needle in result.error.lower()


def test_run_commands_capture_inner_fails_login_with_tail(monkeypatch):
    extractor = _make_extractor(child=SimpleNamespace(before="...login banner..."))
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: False)
    monkeypatch.setattr(extractor, "_handle_login", lambda *args: False)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_capture_inner(
        commands=["show clock"],
        username="admin",
        password="pw",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="",
        attempt_enable=False,
    )
    assert result.success is False
    assert "failed to login" in result.error.lower()
    assert "login banner" in result.error


def test_run_commands_capture_inner_fails_prompt_with_unavailable_buffer(monkeypatch):
    class _Child:
        @property
        def before(self):
            raise RuntimeError("no buffer")

    extractor = _make_extractor(child=_Child())
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda prompt: False)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_capture_inner(
        commands=["show clock"],
        username="",
        password="",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="",
        attempt_enable=False,
    )
    assert result.success is False
    assert "failed to get cli prompt" in result.error.lower()


def test_run_commands_capture_inner_enable_failure(monkeypatch):
    extractor = _make_extractor(child=SimpleNamespace(before=""))
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_enter_enable_mode", lambda *args: False)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_capture_inner(
        commands=["show clock"],
        username="",
        password="",
        enable_password="enable",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="",
        attempt_enable=True,
    )
    assert result.success is False
    assert "enable mode" in result.error.lower()


def test_run_commands_capture_inner_collects_success_and_timeout(monkeypatch):
    extractor = _make_extractor(child=SimpleNamespace(before=""))
    monkeypatch.setattr(extractor, "_spawn_console", lambda: None)
    monkeypatch.setattr(extractor, "_prime_console_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_wait_for_prompt", lambda prompt: True)
    monkeypatch.setattr(extractor, "_disable_paging", lambda *args: None)

    calls = {"count": 0}

    def _execute(command, prompt):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return "hostname r1\n!"

    monkeypatch.setattr(extractor, "_execute_command", _execute)
    monkeypatch.setattr(extractor, "_clean_config", lambda output, command: f"clean::{command}")
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)

    result = extractor._run_commands_capture_inner(
        commands=["show version", "show run"],
        username="",
        password="",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="terminal length 0",
        attempt_enable=False,
    )
    assert result.success is False
    assert result.commands_run == 1
    assert len(result.outputs) == 2
    assert result.outputs[0].success is False
    assert result.outputs[1].output == "clean::show run"
    assert "1 command(s) failed" == result.error


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (console_extractor.pexpect.TIMEOUT("timeout"), "timeout"),
        (console_extractor.pexpect.EOF("closed"), "closed"),
        (RuntimeError("boom"), "boom"),
    ],
)
def test_run_commands_capture_inner_exception_paths(monkeypatch, exc, needle):
    extractor = _make_extractor()

    def _raise():
        raise exc

    monkeypatch.setattr(extractor, "_spawn_console", _raise)
    monkeypatch.setattr(extractor, "_cleanup", lambda: None)
    result = extractor._run_commands_capture_inner(
        commands=["show clock"],
        username="",
        password="",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="",
        attempt_enable=False,
    )
    assert result.success is False
    assert needle in result.error.lower()


def test_extract_vm_config_method_none_returns_error(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    settings = SimpleNamespace(method="none")
    _set_vendors_module(monkeypatch, settings=settings, vendor_config=None)
    result = console_extractor.extract_vm_config("vm1", "kind-none")
    assert result.success is False
    assert "not supported" in result.error.lower()


def test_extract_vm_config_serial_tcp_requires_port_resolution(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    settings = SimpleNamespace(
        method="serial",
        timeout=30,
        command="show run",
        user="admin",
        password="pw",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="terminal length 0",
    )
    vendor_config = SimpleNamespace(serial_type="tcp")
    _set_vendors_module(monkeypatch, settings=settings, vendor_config=vendor_config)
    monkeypatch.setattr(console_extractor, "_get_tcp_serial_port_sync", lambda *args: None)
    result = console_extractor.extract_vm_config("vm1", "kind-tcp")
    assert result.success is False
    assert "could not resolve tcp serial port" in result.error.lower()


def test_extract_vm_config_serial_path_uses_extractor(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    settings = SimpleNamespace(
        method="serial",
        timeout=45,
        command="show running-config",
        user="admin",
        password="pw",
        enable_password="enable",
        prompt_pattern=r"[>#]\s*$",
        paging_disable="terminal length 0",
    )
    _set_vendors_module(
        monkeypatch,
        settings=settings,
        vendor_config=SimpleNamespace(serial_type="pty"),
    )

    seen: dict[str, object] = {}

    class _FakeExtractor:
        def __init__(self, domain_name: str, libvirt_uri: str, timeout: int, tcp_port: int | None = None):
            seen["domain"] = domain_name
            seen["uri"] = libvirt_uri
            seen["timeout"] = timeout
            seen["tcp_port"] = tcp_port

        def extract_config(self, **kwargs):
            seen["extract_kwargs"] = kwargs
            return console_extractor.ExtractionResult(success=True, config="ok")

    monkeypatch.setattr(console_extractor, "SerialConsoleExtractor", _FakeExtractor)

    result = console_extractor.extract_vm_config("vm1", "kind-serial", libvirt_uri="qemu:///test")
    assert result.success is True
    assert seen["timeout"] == 45
    assert seen["tcp_port"] is None
    assert seen["extract_kwargs"]["command"] == "show running-config"


def test_extract_vm_config_unsupported_method_returns_error(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    _set_vendors_module(monkeypatch, settings=SimpleNamespace(method="ssh"), vendor_config=None)
    result = console_extractor.extract_vm_config("vm1", "kind-ssh")
    assert result.success is False
    assert "unsupported extraction method" in result.error.lower()


def test_run_vm_post_boot_commands_returns_missing_pexpect_error(monkeypatch):
    console_extractor.clear_vm_post_boot_cache()
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", False)

    control_calls: list[tuple[str, str, str]] = []
    _set_registry_module(
        monkeypatch,
        set_console_control_state=(
            lambda domain_name, *, state, message: control_calls.append((domain_name, state, message))
        ),
    )

    result = console_extractor.run_vm_post_boot_commands("vm1", "kind1")
    assert result.success is False
    assert "pexpect package is not installed" in result.error
    assert control_calls[0][1] == "read_only"
    assert control_calls[-1][1] == "interactive"
    assert "automation unavailable" in control_calls[-1][2].lower()


def test_run_vm_post_boot_commands_tcp_vendor_passes_tcp_port(monkeypatch):
    console_extractor.clear_vm_post_boot_cache()
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    _set_registry_module(monkeypatch)

    settings = SimpleNamespace(
        user="admin",
        password="pw",
        enable_password="",
        prompt_pattern=r"[>#]\s*$",
    )
    vendor_config = SimpleNamespace(serial_type="tcp", post_boot_commands=["show version"])
    _set_vendors_module(monkeypatch, settings=settings, vendor_config=vendor_config)
    monkeypatch.setattr(console_extractor, "_get_tcp_serial_port_sync", lambda *args: 2201)

    seen: dict[str, object] = {}

    class _FakeExtractor:
        def __init__(self, domain_name: str, libvirt_uri: str, timeout: int, tcp_port: int | None = None):
            seen["tcp_port"] = tcp_port

        def run_commands(self, **kwargs):
            return console_extractor.CommandResult(success=True, commands_run=1)

    monkeypatch.setattr(console_extractor, "SerialConsoleExtractor", _FakeExtractor)

    result = console_extractor.run_vm_post_boot_commands("vm1", "kind-tcp")
    assert result.success is True
    assert seen["tcp_port"] == 2201


def test_run_vm_cli_commands_empty_commands_returns_success():
    result = console_extractor.run_vm_cli_commands("vm1", "kind1", [])
    assert result.success is True
    assert result.commands_run == 0


def test_run_vm_cli_commands_method_none_returns_error(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    _set_vendors_module(
        monkeypatch,
        settings=SimpleNamespace(method="none", timeout=30),
        vendor_config=SimpleNamespace(serial_type="pty"),
    )
    result = console_extractor.run_vm_cli_commands("vm1", "kind-none", ["show clock"])
    assert result.success is False
    assert "not supported" in result.error.lower()


def test_run_vm_cli_commands_tcp_vendor_resolves_port(monkeypatch):
    monkeypatch.setattr(console_extractor, "PEXPECT_AVAILABLE", True)
    settings = SimpleNamespace(
        method="serial",
        user="admin",
        password="pw",
        enable_password="",
        timeout=30,
        prompt_pattern=r"[>#]\s*$",
        paging_disable="terminal length 0",
    )
    _set_vendors_module(
        monkeypatch,
        settings=settings,
        vendor_config=SimpleNamespace(serial_type="tcp"),
    )
    monkeypatch.setattr(console_extractor, "_get_tcp_serial_port_sync", lambda *args: 2202)

    seen: dict[str, object] = {}

    class _FakeExtractor:
        def __init__(self, domain_name: str, libvirt_uri: str, timeout: int, tcp_port: int | None = None):
            seen["tcp_port"] = tcp_port

        def run_commands_capture(self, **kwargs):
            return console_extractor.CommandCaptureResult(success=True, commands_run=1)

    monkeypatch.setattr(console_extractor, "SerialConsoleExtractor", _FakeExtractor)
    result = console_extractor.run_vm_cli_commands("vm1", "kind1", ["show clock"])
    assert result.success is True
    assert seen["tcp_port"] == 2202


def test_clear_vm_post_boot_cache_for_domain_updates_control_state(monkeypatch):
    console_extractor._vm_post_boot_completed.add("vm1")
    console_extractor._vm_post_boot_in_progress.add("vm1")

    calls: list[tuple[str, str, str]] = []
    _set_registry_module(
        monkeypatch,
        set_console_control_state=(
            lambda domain_name, *, state, message: calls.append((domain_name, state, message))
        ),
    )

    console_extractor.clear_vm_post_boot_cache("vm1")
    assert "vm1" not in console_extractor._vm_post_boot_completed
    assert "vm1" not in console_extractor._vm_post_boot_in_progress
    assert calls and calls[0][1] == "interactive"


def test_clear_vm_post_boot_cache_ignores_console_state_errors(monkeypatch):
    console_extractor._vm_post_boot_completed.add("vm2")
    console_extractor._vm_post_boot_in_progress.add("vm2")

    _set_registry_module(
        monkeypatch,
        set_console_control_state=(
            lambda domain_name, *, state, message: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    )

    console_extractor.clear_vm_post_boot_cache("vm2")
    assert "vm2" not in console_extractor._vm_post_boot_completed
    assert "vm2" not in console_extractor._vm_post_boot_in_progress
