from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.console_extractor as console_mod
import agent.providers.libvirt_readiness as readiness_mod
from agent.readiness import ReadinessResult


class _SocketCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture(autouse=True)
def _inline_to_thread(monkeypatch):
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(readiness_mod.asyncio, "to_thread", _run_inline)


def _install_fake_libvirt(monkeypatch, running_state: int = 1):
    monkeypatch.setitem(
        sys.modules,
        "libvirt",
        SimpleNamespace(VIR_DOMAIN_RUNNING=running_state),
    )


def _make_check_kwargs(
    *,
    result_tuple=(1, {}),
    ip: str | None = None,
    canonical_kind: str = "iosv",
):
    async def _run_libvirt(_func, _domain_name):
        return result_tuple

    async def _get_ip(_domain_name):
        return ip

    return {
        "domain_name": "arch-lab1-node1",
        "uri": "qemu:///system",
        "run_libvirt_fn": _run_libvirt,
        "check_readiness_domain_sync_fn": lambda _domain_name: result_tuple,
        "get_vm_management_ip_fn": _get_ip,
        "canonical_kind_fn": lambda _kind: canonical_kind,
        "clear_console_state_fn": lambda _domain_name: None,
        "loader_recovery_attempts": {},
        "loader_recovery_last_at": {},
        "loader_max_attempts": 5,
        "loader_cooldown": 30.0,
        "panic_recovery_attempts": {},
        "panic_recovery_last_at": {},
        "panic_last_log_size": {},
        "panic_max_attempts": 3,
        "panic_cooldown": 60.0,
        "poap_skip_attempted": set(),
        "admin_password_completed": set(),
        "conn": object(),
    }


def test_extract_probe_markers_variants():
    assert readiness_mod.extract_probe_markers(None) == set()
    assert readiness_mod.extract_probe_markers("foo=bar") == set()
    assert readiness_mod.extract_probe_markers("markers=none") == set()
    assert readiness_mod.extract_probe_markers("x=1; markers=loader_prompt, poap_abort_prompt ; y=2") == {
        "loader_prompt",
        "poap_abort_prompt",
    }


def test_classify_console_result_variants():
    ok = SimpleNamespace(success=True, outputs=[], error="")
    assert readiness_mod.classify_console_result(ok) == "sent"

    handoff = SimpleNamespace(
        success=False,
        outputs=[SimpleNamespace(error="Timeout waiting for command output")],
        error="",
    )
    assert readiness_mod.classify_console_result(handoff) == "sent_handoff_timeout"

    closed = SimpleNamespace(
        success=False,
        outputs=[SimpleNamespace(error="random")],
        error="Console connection closed unexpectedly",
    )
    assert readiness_mod.classify_console_result(closed) == "sent_console_closed"

    failed = SimpleNamespace(success=False, outputs=[], error="boom")
    assert readiness_mod.classify_console_result(failed) == "failed"


def test_check_tcp_port_success_and_failure(monkeypatch):
    monkeypatch.setattr(readiness_mod.socket, "create_connection", lambda *_args, **_kwargs: _SocketCtx())
    assert readiness_mod.check_tcp_port("127.0.0.1", 22, 0.1) is True

    def _raise(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(readiness_mod.socket, "create_connection", _raise)
    assert readiness_mod.check_tcp_port("127.0.0.1", 22, 0.1) is False


@pytest.mark.asyncio
async def test_run_post_boot_commands_skips_n9kv_when_boot_mods_disabled(monkeypatch):
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", False, raising=False)
    cleared: list[str] = []

    ok = await readiness_mod.run_post_boot_commands(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        canonical_kind_fn=lambda _kind: "cisco_n9kv",
        clear_console_state_fn=cleared.append,
    )
    assert ok is True
    assert cleared == ["arch-lab1-node1"]


@pytest.mark.asyncio
async def test_run_post_boot_commands_skips_when_pexpect_unavailable(monkeypatch):
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_poap_preboot_enabled", True, raising=False)
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", False)

    ok = await readiness_mod.run_post_boot_commands(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        canonical_kind_fn=lambda _kind: "cisco_n9kv",
        clear_console_state_fn=lambda _name: None,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_run_post_boot_commands_executes_and_returns_result(monkeypatch):
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_poap_preboot_enabled", False, raising=False)
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)

    called = {}

    def _runner(domain_name, kind, uri):
        called["args"] = (domain_name, kind, uri)
        return SimpleNamespace(success=False)

    monkeypatch.setattr(console_mod, "run_vm_post_boot_commands", _runner)

    ok = await readiness_mod.run_post_boot_commands(
        "arch-lab1-node1",
        "iosv",
        "qemu:///system",
        canonical_kind_fn=lambda _kind: "iosv",
        clear_console_state_fn=lambda _name: None,
    )
    assert ok is False
    assert called["args"] == ("arch-lab1-node1", "iosv", "qemu:///system")


@pytest.mark.asyncio
async def test_run_post_boot_commands_handles_exception(monkeypatch):
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(readiness_mod.asyncio, "to_thread", _raise)

    ok = await readiness_mod.run_post_boot_commands(
        "arch-lab1-node1",
        "iosv",
        "qemu:///system",
        canonical_kind_fn=lambda _kind: "iosv",
        clear_console_state_fn=lambda _name: None,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_run_n9kv_loader_recovery_guard_paths(monkeypatch):
    attempts = {"arch-lab1-node1": 5}
    last_at = {}
    assert (
        await readiness_mod.run_n9kv_loader_recovery(
            "arch-lab1-node1",
            "cisco_n9kv",
            "qemu:///system",
            recovery_attempts=attempts,
            recovery_last_at=last_at,
            max_attempts=5,
            cooldown=30,
        )
        == "skipped_max_attempts"
    )

    attempts = {"arch-lab1-node1": 1}
    last_at = {"arch-lab1-node1": 100.0}
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 120.0)
    assert (
        await readiness_mod.run_n9kv_loader_recovery(
            "arch-lab1-node1",
            "cisco_n9kv",
            "qemu:///system",
            recovery_attempts=attempts,
            recovery_last_at=last_at,
            max_attempts=5,
            cooldown=30,
        )
        == "skipped_cooldown"
    )


@pytest.mark.asyncio
async def test_run_n9kv_loader_recovery_pexpect_unavailable(monkeypatch):
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", False)
    status = await readiness_mod.run_n9kv_loader_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        recovery_attempts={},
        recovery_last_at={},
        max_attempts=5,
        cooldown=30,
    )
    assert status == "skipped_pexpect_unavailable"


@pytest.mark.asyncio
async def test_run_n9kv_loader_recovery_error_updates_state(monkeypatch):
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)
    attempts = {}
    last_at = {}

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("oops")

    monkeypatch.setattr(readiness_mod.asyncio, "to_thread", _raise)
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 123.0)

    status = await readiness_mod.run_n9kv_loader_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        recovery_attempts=attempts,
        recovery_last_at=last_at,
        max_attempts=5,
        cooldown=30,
    )
    assert status == "error"
    assert attempts["arch-lab1-node1"] == 1
    assert last_at["arch-lab1-node1"] == 123.0


@pytest.mark.asyncio
async def test_run_n9kv_loader_recovery_success_updates_state(monkeypatch):
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)
    attempts = {"arch-lab1-node1": 1}
    last_at = {"arch-lab1-node1": 0.0}

    monkeypatch.setattr(
        console_mod,
        "run_vm_cli_commands",
        lambda **_kwargs: SimpleNamespace(
            success=True,
            outputs=[],
            error="",
            commands_run=1,
        ),
    )
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(readiness_mod, "classify_console_result", lambda _result: "sent")

    status = await readiness_mod.run_n9kv_loader_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        recovery_attempts=attempts,
        recovery_last_at=last_at,
        max_attempts=5,
        cooldown=30,
    )
    assert status == "sent"
    assert attempts["arch-lab1-node1"] == 2
    assert last_at["arch-lab1-node1"] == 200.0


@pytest.mark.asyncio
async def test_run_n9kv_panic_recovery_guard_and_staleness_paths(monkeypatch, tmp_path):
    serial_log = tmp_path / "serial.log"
    serial_log.write_text("abc", encoding="utf-8")

    attempts = {"arch-lab1-node1": 3}
    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=AsyncMock(),
        conn=object(),
        panic_attempts=attempts,
        panic_last_at={},
        panic_last_log_size={},
        max_attempts=3,
        cooldown=60,
    )
    assert status == "skipped_max_attempts"

    attempts = {"arch-lab1-node1": 1}
    last_at = {"arch-lab1-node1": 100.0}
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 120.0)
    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=AsyncMock(),
        conn=object(),
        panic_attempts=attempts,
        panic_last_at=last_at,
        panic_last_log_size={},
        max_attempts=3,
        cooldown=60,
    )
    assert status == "skipped_cooldown"

    panic_sizes = {}
    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=AsyncMock(),
        conn=object(),
        panic_attempts={},
        panic_last_at={},
        panic_last_log_size=panic_sizes,
        max_attempts=3,
        cooldown=0,
    )
    assert status == "skipped_first_detection"
    assert panic_sizes["arch-lab1-node1"] == serial_log.stat().st_size

    serial_log.write_text("abcdef", encoding="utf-8")
    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=AsyncMock(),
        conn=object(),
        panic_attempts={},
        panic_last_at={},
        panic_last_log_size={"arch-lab1-node1": 1},
        max_attempts=3,
        cooldown=0,
    )
    assert status == "skipped_log_growing"


@pytest.mark.asyncio
async def test_run_n9kv_panic_recovery_handles_missing_serial_log(monkeypatch):
    monkeypatch.setattr(readiness_mod.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError("missing")))
    panic_sizes = {}
    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        "/tmp/does-not-exist.log",
        run_libvirt_fn=AsyncMock(),
        conn=object(),
        panic_attempts={},
        panic_last_at={},
        panic_last_log_size=panic_sizes,
        max_attempts=3,
        cooldown=0,
    )
    assert status == "skipped_first_detection"
    assert panic_sizes["arch-lab1-node1"] == -1


@pytest.mark.asyncio
async def test_run_n9kv_panic_recovery_handles_restart_error(monkeypatch, tmp_path):
    serial_log = tmp_path / "serial.log"
    serial_log.write_text("abc", encoding="utf-8")
    attempts = {}
    last_at = {}
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 77.0)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("fail")

    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=_raise,
        conn=object(),
        panic_attempts=attempts,
        panic_last_at=last_at,
        panic_last_log_size={"arch-lab1-node1": serial_log.stat().st_size},
        max_attempts=3,
        cooldown=0,
    )
    assert status == "error"
    assert attempts["arch-lab1-node1"] == 1
    assert last_at["arch-lab1-node1"] == 77.0


@pytest.mark.asyncio
async def test_run_n9kv_panic_recovery_restarts_and_clears_state(monkeypatch, tmp_path):
    serial_log = tmp_path / "serial.log"
    serial_log.write_text("abc", encoding="utf-8")
    attempts = {}
    last_at = {}
    log_sizes = {"arch-lab1-node1": serial_log.stat().st_size}
    monkeypatch.setattr(readiness_mod.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(readiness_mod.time, "monotonic", lambda: 88.0)

    ops: list[str] = []

    class _Domain:
        def destroy(self):
            ops.append("destroy")

        def create(self):
            ops.append("create")

    class _Conn:
        def lookupByName(self, _name):
            return _Domain()

    async def _run_libvirt(func, conn, domain_name):
        func(conn, domain_name)

    status = await readiness_mod.run_n9kv_panic_recovery(
        "arch-lab1-node1",
        "cisco_n9kv",
        str(serial_log),
        run_libvirt_fn=_run_libvirt,
        conn=_Conn(),
        panic_attempts=attempts,
        panic_last_at=last_at,
        panic_last_log_size=log_sizes,
        max_attempts=3,
        cooldown=0,
    )
    assert status == "restarted"
    assert ops == ["destroy", "create"]
    assert "arch-lab1-node1" not in log_sizes
    assert attempts["arch-lab1-node1"] == 1
    assert last_at["arch-lab1-node1"] == 88.0


@pytest.mark.asyncio
async def test_run_n9kv_poap_skip_paths(monkeypatch):
    attempted = {"arch-lab1-node1"}
    status = await readiness_mod.run_n9kv_poap_skip(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        poap_skip_attempted=attempted,
    )
    assert status == "skipped_already_attempted"

    attempted = set()
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", False)
    status = await readiness_mod.run_n9kv_poap_skip(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        poap_skip_attempted=attempted,
    )
    assert status == "skipped_pexpect_unavailable"
    assert "arch-lab1-node1" in attempted


@pytest.mark.asyncio
async def test_run_n9kv_poap_skip_error_and_success(monkeypatch):
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)
    attempted = set()

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(readiness_mod.asyncio, "to_thread", _raise)
    status = await readiness_mod.run_n9kv_poap_skip(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        poap_skip_attempted=attempted,
    )
    assert status == "error"

    attempted = set()
    monkeypatch.setattr(
        console_mod,
        "run_vm_cli_commands",
        lambda **_kwargs: SimpleNamespace(success=True, outputs=[], error="", commands_run=1),
    )
    monkeypatch.setattr(readiness_mod, "classify_console_result", lambda _result: "sent")
    monkeypatch.setattr(
        readiness_mod.asyncio,
        "to_thread",
        lambda func, *args, **kwargs: _awaitable(func(*args, **kwargs)),
    )

    status = await readiness_mod.run_n9kv_poap_skip(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        poap_skip_attempted=attempted,
    )
    assert status == "sent"


@pytest.mark.asyncio
async def test_run_n9kv_admin_password_setup_paths(monkeypatch):
    completed = {"arch-lab1-node1"}
    status = await readiness_mod.run_n9kv_admin_password_setup(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        admin_password_completed=completed,
    )
    assert status == "skipped_already_completed"

    completed = set()
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", False)
    status = await readiness_mod.run_n9kv_admin_password_setup(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        admin_password_completed=completed,
    )
    assert status == "skipped_pexpect_unavailable"

    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(readiness_mod.asyncio, "to_thread", _raise)
    status = await readiness_mod.run_n9kv_admin_password_setup(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        admin_password_completed=completed,
    )
    assert status == "error"


@pytest.mark.asyncio
async def test_run_n9kv_admin_password_setup_sent_updates_completed(monkeypatch):
    monkeypatch.setattr(console_mod, "PEXPECT_AVAILABLE", True)
    monkeypatch.setattr(
        console_mod,
        "run_vm_cli_commands",
        lambda **_kwargs: SimpleNamespace(success=True, outputs=[], error="", commands_run=1),
    )
    monkeypatch.setattr(readiness_mod, "classify_console_result", lambda _result: "sent")
    completed = set()

    status = await readiness_mod.run_n9kv_admin_password_setup(
        "arch-lab1-node1",
        "cisco_n9kv",
        "qemu:///system",
        admin_password_completed=completed,
    )
    assert status == "sent"
    assert "arch-lab1-node1" in completed


@pytest.mark.asyncio
async def test_check_readiness_returns_unavailable_without_libvirt(monkeypatch):
    monkeypatch.delitem(sys.modules, "libvirt", raising=False)
    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(),
    )
    assert result.is_ready is False
    assert result.message == "libvirt not available"


@pytest.mark.asyncio
async def test_check_readiness_domain_not_found_and_not_running(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)

    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(result_tuple=None),
    )
    assert result.message == "VM domain not found"

    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(result_tuple=(5, {})),
    )
    assert result.message == "VM not running (state=5)"


@pytest.mark.asyncio
async def test_check_readiness_ssh_gating_paths(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "ssh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="ssh"),
    )

    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(ip=None),
    )
    assert result.message == "Waiting for management IP"
    assert result.progress_percent == 30

    monkeypatch.setattr(readiness_mod, "check_tcp_port", lambda *_args, **_kwargs: False)
    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(ip="192.0.2.10"),
    )
    assert "waiting for SSH" in result.message
    assert result.progress_percent == 70

    monkeypatch.setattr(readiness_mod, "check_tcp_port", lambda *_args, **_kwargs: True)
    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(ip="192.0.2.10"),
    )
    assert result.is_ready is True
    assert result.progress_percent == 100


@pytest.mark.asyncio
async def test_check_readiness_non_ssh_probe_runs_post_boot_when_ready(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="log_pattern"),
    )
    monkeypatch.setattr(readiness_mod.settings, "workspace_path", "/tmp/workspace", raising=False)

    class _Probe:
        async def check(self, _node_name):
            return ReadinessResult(is_ready=True, message="Boot complete", progress_percent=100)

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _Probe())
    post_boot = AsyncMock()
    monkeypatch.setattr(readiness_mod, "run_post_boot_commands", post_boot)

    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "iosv",
        **_make_check_kwargs(result_tuple=(1, {})),
    )
    assert result.is_ready is True
    assert post_boot.await_count == 1


@pytest.mark.asyncio
async def test_check_readiness_loader_recovery_message_paths(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod.settings, "workspace_path", "/tmp/workspace", raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="log_pattern"),
    )

    class _Probe:
        async def check(self, _node_name):
            return ReadinessResult(
                is_ready=False,
                message="Boot blocked",
                progress_percent=10,
                details="markers=loader_prompt",
            )

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _Probe())
    monkeypatch.setattr(readiness_mod, "run_n9kv_loader_recovery", AsyncMock(return_value="sent"))

    kwargs = _make_check_kwargs(result_tuple=(1, {}), canonical_kind="cisco_n9kv")
    kwargs["loader_recovery_attempts"] = {"arch-lab1-node1": 2}
    result = await readiness_mod.check_readiness("lab1", "node1", "cisco_n9kv", **kwargs)
    assert "Boot recovery in progress" in result.message
    assert "loader_recovery=sent" in (result.details or "")

    monkeypatch.setattr(
        readiness_mod,
        "run_n9kv_loader_recovery",
        AsyncMock(return_value="skipped_max_attempts"),
    )
    result = await readiness_mod.check_readiness("lab1", "node1", "cisco_n9kv", **kwargs)
    assert result.message == "Boot recovery exhausted (5 attempts)"

    monkeypatch.setattr(
        readiness_mod,
        "run_n9kv_loader_recovery",
        AsyncMock(return_value="skipped_cooldown"),
    )
    result = await readiness_mod.check_readiness("lab1", "node1", "cisco_n9kv", **kwargs)
    assert "Boot recovery cooling down" in result.message


@pytest.mark.asyncio
async def test_check_readiness_poap_paths(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod.settings, "workspace_path", "/tmp/workspace", raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_poap_preboot_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="log_pattern"),
    )

    class _ProbePrompt:
        async def check(self, _node_name):
            return ReadinessResult(
                is_ready=False,
                message="waiting",
                progress_percent=20,
                details="markers=poap_abort_prompt",
            )

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _ProbePrompt())
    skip = AsyncMock(return_value="sent")
    monkeypatch.setattr(readiness_mod, "run_n9kv_poap_skip", skip)
    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "cisco_n9kv",
        **_make_check_kwargs(result_tuple=(1, {}), canonical_kind="cisco_n9kv"),
    )
    assert result.message == "POAP provisioning in progress"
    assert skip.await_count == 0

    class _ProbeFail:
        async def check(self, _node_name):
            return ReadinessResult(
                is_ready=False,
                message="waiting",
                progress_percent=20,
                details="markers=poap_failure",
            )

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _ProbeFail())
    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "cisco_n9kv",
        **_make_check_kwargs(result_tuple=(1, {}), canonical_kind="cisco_n9kv"),
    )
    assert result.message == "POAP skip in progress (skipping to normal setup)"
    assert "poap_skip=sent" in (result.details or "")


@pytest.mark.asyncio
async def test_check_readiness_admin_password_path(monkeypatch):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod.settings, "workspace_path", "/tmp/workspace", raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="log_pattern"),
    )

    class _Probe:
        async def check(self, _node_name):
            return ReadinessResult(
                is_ready=False,
                message="waiting",
                progress_percent=20,
                details="markers=admin_password_prompt",
            )

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _Probe())
    monkeypatch.setattr(readiness_mod, "run_n9kv_admin_password_setup", AsyncMock(return_value="sent"))

    result = await readiness_mod.check_readiness(
        "lab1",
        "node1",
        "cisco_n9kv",
        **_make_check_kwargs(result_tuple=(1, {}), canonical_kind="cisco_n9kv"),
    )
    assert result.is_ready is True
    assert result.progress_percent == 100
    assert result.message == "Boot complete (admin password configured)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("panic_status", "expected"),
    [
        ("restarted", "Kernel panic detected — restarting VM"),
        ("skipped_first_detection", "Kernel panic detected — monitoring for recovery"),
        ("skipped_log_growing", "Kernel panic detected — VM recovering"),
        ("skipped_max_attempts", "Kernel panic recovery exhausted"),
        ("skipped_cooldown", "Kernel panic recovery cooling down"),
    ],
)
async def test_check_readiness_kernel_panic_status_messages(monkeypatch, panic_status, expected):
    _install_fake_libvirt(monkeypatch, running_state=1)
    monkeypatch.setattr(readiness_mod.settings, "workspace_path", "/tmp/workspace", raising=False)
    monkeypatch.setattr(readiness_mod.settings, "n9kv_boot_modifications_enabled", True, raising=False)
    monkeypatch.setattr(readiness_mod, "get_console_method", lambda _kind: "virsh")
    monkeypatch.setattr(
        readiness_mod,
        "get_libvirt_config",
        lambda _kind: SimpleNamespace(readiness_probe="log_pattern"),
    )

    class _Probe:
        async def check(self, _node_name):
            return ReadinessResult(
                is_ready=False,
                message="waiting",
                progress_percent=20,
                details="markers=kernel_panic",
            )

    monkeypatch.setattr(readiness_mod, "get_libvirt_probe", lambda *_args, **_kwargs: _Probe())
    monkeypatch.setattr(readiness_mod, "run_n9kv_panic_recovery", AsyncMock(return_value=panic_status))

    kwargs = _make_check_kwargs(result_tuple=(1, {}), canonical_kind="cisco_n9kv")
    kwargs["panic_recovery_attempts"] = {"arch-lab1-node1": 1}
    result = await readiness_mod.check_readiness("lab1", "node1", "cisco_n9kv", **kwargs)
    assert expected in result.message
    assert f"panic_recovery={panic_status}" in (result.details or "")


def _awaitable(value):
    async def _inner():
        return value

    return _inner()
