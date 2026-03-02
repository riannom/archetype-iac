from __future__ import annotations

import builtins
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.network.interface_config as iface


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _inline_to_thread(monkeypatch):
    async def _run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(iface.asyncio, "to_thread", _run_inline)


def test_run_on_host_uses_nsenter_in_container(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda cmd, **_kwargs: calls.append(cmd) or _Result(returncode=0),
    )

    iface._run_on_host(["nmcli", "general"], timeout=5)
    assert calls[0][:5] == ["nsenter", "-t", "1", "-m", "--"]


def test_run_on_host_without_container(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(iface, "_is_in_container", lambda: False)
    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda cmd, **_kwargs: calls.append(cmd) or _Result(returncode=0),
    )

    iface._run_on_host(["ip", "link"], timeout=5)
    assert calls[0] == ["ip", "link"]


def test_host_glob_and_read_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda *_args, **_kwargs: _Result(returncode=0, stdout="/etc/netplan/01.yaml\n/etc/netplan/02.yaml\n"),
    )
    files = iface._host_glob("/etc/netplan", "*.yaml")
    assert files == ["/etc/netplan/01.yaml", "/etc/netplan/02.yaml"]

    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda *_args, **_kwargs: _Result(returncode=1, stdout="", stderr="no"),
    )
    assert iface._host_read_file("/etc/hosts") is None

    monkeypatch.setattr(iface, "_is_in_container", lambda: False)
    assert iface._host_glob(str(tmp_path), "*.yaml") == []


def test_host_write_file_and_mkdir_error_paths(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_args, **_kwargs: _Result(returncode=1, stderr="denied"),
    )
    ok, err = iface._host_write_file("/etc/x", "abc")
    assert ok is False
    assert "denied" in (err or "")

    monkeypatch.setattr(iface.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, err = iface._host_write_file("/etc/x", "abc")
    assert ok is False
    assert "boom" in (err or "")

    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda *_args, **_kwargs: _Result(returncode=1, stderr="mkdir failed"),
    )
    ok, err = iface._host_mkdir("/etc/systemd/network")
    assert ok is False
    assert "mkdir failed" in (err or "")


def test_detect_network_manager_branches(monkeypatch):
    # networkmanager
    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda cmd, **_kwargs: _Result(returncode=0, stdout="running") if cmd[:1] == ["nmcli"] else _Result(1),
    )
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    assert iface.detect_network_manager() == "networkmanager"

    # netplan
    def _run_netplan(cmd, **_kwargs):
        if cmd[:1] == ["nmcli"]:
            return _Result(returncode=1)
        if cmd[:2] == ["which", "netplan"]:
            return _Result(returncode=0)
        return _Result(returncode=1)

    monkeypatch.setattr(iface, "_run_on_host", _run_netplan)
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: ["/etc/netplan/01.yaml"])
    assert iface.detect_network_manager() == "netplan"

    # systemd-networkd
    def _run_networkd(cmd, **_kwargs):
        if cmd[:1] == ["nmcli"]:
            return _Result(returncode=1)
        if cmd[:2] == ["systemctl", "is-active"]:
            return _Result(returncode=0, stdout="active")
        return _Result(returncode=1)

    monkeypatch.setattr(iface, "_run_on_host", _run_networkd)
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    assert iface.detect_network_manager() == "systemd-networkd"

    # unknown
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    assert iface.detect_network_manager() == "unknown"


@pytest.mark.asyncio
async def test_set_mtu_runtime_timeout_and_exception(monkeypatch):
    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(iface.subprocess.TimeoutExpired("ip", 1)),
    )
    ok, err = await iface.set_mtu_runtime("eth0", 9000)
    assert ok is False
    assert err == "Command timed out"

    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ok, err = await iface.set_mtu_runtime("eth0", 9000)
    assert ok is False
    assert "boom" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_networkmanager_branches(monkeypatch):
    # list connections failed
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: _Result(returncode=1, stderr="list fail"))
    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 9000)
    assert ok is False
    assert "Failed to list connections" in (err or "")

    # modify failed
    calls = {"count": 0}

    def _run_modify(cmd, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Result(returncode=0, stdout="conn0:eth0\n")
        if "modify" in cmd:
            return _Result(returncode=1, stderr="modify fail")
        return _Result(returncode=0)

    monkeypatch.setattr(iface, "_run_on_host", _run_modify)
    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 9000)
    assert ok is False
    assert "Failed to modify connection" in (err or "")

    # connection up warning but overall success
    calls["count"] = 0

    def _run_up_warn(cmd, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Result(returncode=0, stdout="conn0:eth0\n")
        if "modify" in cmd:
            return _Result(returncode=0)
        return _Result(returncode=1, stderr="warn")

    monkeypatch.setattr(iface, "_run_on_host", _run_up_warn)
    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 9000)
    assert ok is True
    assert err is None

    # timeout
    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(iface.subprocess.TimeoutExpired("nmcli", 1)),
    )
    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 9000)
    assert ok is False
    assert err == "Command timed out"


@pytest.mark.asyncio
async def test_set_mtu_persistent_netplan_import_and_write_failures(monkeypatch):
    orig_import = builtins.__import__

    def _import_fail(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("missing")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_fail)
    ok, err = await iface.set_mtu_persistent_netplan("eth0", 9000)
    assert ok is False
    assert "PyYAML not installed" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_netplan_update_and_apply_paths(monkeypatch):
    fake_yaml = SimpleNamespace(
        safe_load=lambda _content: {"network": {"ethernets": {"eth0": {}}}},
        dump=lambda data, **_kwargs: json.dumps(data),
    )
    monkeypatch.setitem(sys.modules, "yaml", fake_yaml)

    # existing file with write failure
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: ["/etc/netplan/01.yaml"])
    monkeypatch.setattr(iface, "_host_read_file", lambda *_args, **_kwargs: "network:\n  version: 2\n")
    monkeypatch.setattr(iface, "_host_write_file", lambda *_args, **_kwargs: (False, "write denied"))
    ok, err = await iface.set_mtu_persistent_netplan("eth0", 9000)
    assert ok is False
    assert "Failed to write netplan config" in (err or "")

    # new file path with netplan apply failure
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(iface, "_host_write_file", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: _Result(returncode=1, stderr="apply fail"))
    ok, err = await iface.set_mtu_persistent_netplan("eth1", 9100)
    assert ok is False
    assert "netplan apply failed" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_systemd_networkd_branches(monkeypatch):
    # mkdir failed
    monkeypatch.setattr(iface, "_host_mkdir", lambda *_args, **_kwargs: (False, "mkdir fail"))
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9000)
    assert ok is False
    assert "Failed to create networkd directory" in (err or "")

    # existing file read failed
    monkeypatch.setattr(iface, "_host_mkdir", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: ["/etc/systemd/network/10-eth0.network"])
    read_calls = {"count": 0}

    def _read_file(*_args, **_kwargs):
        read_calls["count"] += 1
        if read_calls["count"] == 1:
            return "[Match]\nName=eth0\n"
        return None

    monkeypatch.setattr(iface, "_host_read_file", _read_file)
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9000)
    assert ok is False
    assert "Failed to read existing config" in (err or "")

    # write failed
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(iface, "_host_write_file", lambda *_args, **_kwargs: (False, "write fail"))
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9000)
    assert ok is False
    assert "Failed to write config" in (err or "")

    # reload+restart failed
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(iface, "_host_write_file", lambda *_args, **_kwargs: (True, None))
    run_calls = {"count": 0}

    def _run_reload_restart(*_args, **_kwargs):
        run_calls["count"] += 1
        return _Result(returncode=1, stderr="svc fail")

    monkeypatch.setattr(iface, "_run_on_host", _run_reload_restart)
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9000)
    assert ok is False
    assert "Failed to reload systemd-networkd" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_wrapper_dispatch(monkeypatch):
    monkeypatch.setattr(iface, "set_mtu_persistent_networkmanager", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(iface, "set_mtu_persistent_netplan", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(iface, "set_mtu_persistent_systemd_networkd", AsyncMock(return_value=(True, None)))

    assert await iface.set_mtu_persistent("eth0", 9000, "networkmanager") == (True, None)
    assert await iface.set_mtu_persistent("eth0", 9000, "netplan") == (True, None)
    assert await iface.set_mtu_persistent("eth0", 9000, "systemd-networkd") == (True, None)
    assert await iface.set_mtu_persistent("eth0", 9000, "unknown") == (
        False,
        "Unknown network manager - cannot persist MTU configuration",
    )


def test_get_interface_mtu_and_max_and_physical_and_default_route(monkeypatch, tmp_path):
    class _FakePath:
        def __init__(self, p: str):
            self.path = p

        def exists(self):
            if self.path.endswith("/mtu"):
                return True
            if self.path.startswith("/sys/devices/virtual/net/eth0"):
                return False
            if self.path.startswith("/sys/class/net/eth0/device"):
                return True
            return False

        def read_text(self):
            if self.path.endswith("/mtu"):
                return "9000\n"
            raise RuntimeError("bad read")

    monkeypatch.setattr(iface, "Path", _FakePath)
    assert iface.get_interface_mtu("eth0") == 9000

    # max MTU parse
    def _run(cmd, **_kwargs):
        if cmd[:2] == ["ethtool", "-i"]:
            return _Result(returncode=0, stdout="")
        return _Result(returncode=0, stdout='[{"max_mtu": 9216}]')

    monkeypatch.setattr(iface.subprocess, "run", _run)
    assert iface.get_interface_max_mtu("eth0") == 9216

    monkeypatch.setattr(iface.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("oops")))
    assert iface.get_interface_max_mtu("eth0") is None

    assert iface.is_physical_interface("docker0") is False
    assert iface.is_physical_interface("eth0") is True

    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_args, **_kwargs: _Result(returncode=0, stdout='[{"dev": "eth0"}]'),
    )
    assert iface.get_default_route_interface() == "eth0"

    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no route")),
    )
    assert iface.get_default_route_interface() is None
