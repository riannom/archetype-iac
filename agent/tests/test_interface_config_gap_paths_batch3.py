from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import agent.network.interface_config as iface


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_is_in_container_and_host_helpers_remaining_branches(monkeypatch, tmp_path):
    class _PathDockerenv:
        def __init__(self, p: str):
            self.p = p

        def exists(self):
            return self.p == "/.dockerenv"

        def read_text(self):
            raise RuntimeError("unused")

        def glob(self, _pattern):
            return []

        def write_text(self, _content):
            raise RuntimeError("write fail")

        def mkdir(self, parents=True, exist_ok=True):
            raise RuntimeError("mkdir fail")

    monkeypatch.setattr(iface, "Path", _PathDockerenv)
    assert iface._is_in_container() is True

    class _PathCgroupDocker:
        def __init__(self, p: str):
            self.p = p

        def exists(self):
            return self.p == "/proc/1/cgroup"

        def read_text(self):
            return "0::/docker/abc"

        def glob(self, _pattern):
            return []

        def write_text(self, _content):
            raise RuntimeError("write fail")

        def mkdir(self, parents=True, exist_ok=True):
            raise RuntimeError("mkdir fail")

    monkeypatch.setattr(iface, "Path", _PathCgroupDocker)
    monkeypatch.delenv("container", raising=False)
    assert iface._is_in_container() is True

    class _PathCgroupError:
        def __init__(self, p: str):
            self.p = p

        def exists(self):
            return self.p == "/proc/1/cgroup"

        def read_text(self):
            raise RuntimeError("read fail")

        def glob(self, _pattern):
            return []

        def write_text(self, _content):
            raise RuntimeError("write fail")

        def mkdir(self, parents=True, exist_ok=True):
            raise RuntimeError("mkdir fail")

    monkeypatch.delenv("container", raising=False)
    monkeypatch.setattr(iface, "Path", _PathCgroupError)
    assert iface._is_in_container() is False
    assert iface._host_glob("/tmp", "*.yaml") == []
    assert iface._host_read_file("/tmp/nope") is None
    ok, err = iface._host_write_file("/tmp/nope", "x")
    assert ok is False
    assert "write fail" in (err or "")
    ok, err = iface._host_mkdir("/tmp/nope")
    assert ok is False
    assert "mkdir fail" in (err or "")

    monkeypatch.setattr(iface, "Path", Path)
    monkeypatch.setattr(iface, "_is_in_container", lambda: False)
    ok, err = iface._host_mkdir(str(tmp_path / "ok-dir"))
    assert ok is True
    assert err is None


def test_host_glob_container_empty_stdout(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda *_args, **_kwargs: _Result(returncode=0, stdout=""),
    )
    assert iface._host_glob("/etc/netplan", "*.yaml") == []


def test_host_read_write_mkdir_success_branches(monkeypatch, tmp_path):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(
        iface,
        "_run_on_host",
        lambda cmd, **_kwargs: _Result(returncode=0, stdout="content\n")
        if cmd[:1] == ["cat"]
        else _Result(returncode=0),
    )
    monkeypatch.setattr(
        iface.subprocess,
        "run",
        lambda *_a, **_k: _Result(returncode=0),
    )

    assert iface._host_read_file("/etc/hosts") == "content\n"
    ok, err = iface._host_write_file("/etc/hosts", "x")
    assert ok is True
    assert err is None
    ok, err = iface._host_mkdir("/etc/systemd/network")
    assert ok is True
    assert err is None

    monkeypatch.setattr(iface, "_is_in_container", lambda: False)
    path = tmp_path / "ok.txt"
    ok, err = iface._host_write_file(str(path), "hello")
    assert ok is True
    assert err is None
    assert path.read_text() == "hello"


def test_detect_network_manager_remaining_branches(monkeypatch):
    # In-container log branch + netplan `which` exception branch.
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/netplan/01.yaml"])

    def _run(cmd, **_kwargs):
        if cmd[:1] == ["nmcli"]:
            return _Result(returncode=1)
        if cmd[:2] == ["which", "netplan"]:
            raise RuntimeError("which failed")
        if cmd[:2] == ["systemctl", "is-active"]:
            return _Result(returncode=1)
        return _Result(returncode=1)

    monkeypatch.setattr(iface, "_run_on_host", _run)
    assert iface.detect_network_manager() == "unknown"


@pytest.mark.asyncio
async def test_set_mtu_persistent_networkmanager_generic_exception(monkeypatch):
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 9000)
    assert ok is False
    assert "boom" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_netplan_remaining_branches(monkeypatch):
    fake_yaml = type(
        "Y",
        (),
        {
            "safe_load": staticmethod(lambda content: {"network": {"ethernets": {"eth0": {}}}} if "ok" in content else {}),
            "dump": staticmethod(lambda data, **_k: json.dumps(data)),
        },
    )
    monkeypatch.setitem(sys.modules, "yaml", fake_yaml)

    # 370/374/384-386: empty content, missing network key, and file parse error.
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/netplan/a.yaml", "/etc/netplan/b.yaml", "/etc/netplan/c.yaml"])
    calls = {"n": 0}

    def _read(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # line 370
        if calls["n"] == 2:
            return "bad"  # safe_load => {} line 374
        raise RuntimeError("parse fail")  # 384-386

    monkeypatch.setattr(iface, "_host_read_file", _read)
    monkeypatch.setattr(iface, "_host_write_file", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: _Result(returncode=0))
    ok, err = await iface.set_mtu_persistent_netplan("eth9", 9100)
    assert ok is True

    # 421-424: exception from write/apply block
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/netplan/01.yaml"])
    monkeypatch.setattr(iface, "_host_read_file", lambda *_a, **_k: "ok")
    monkeypatch.setattr(iface, "_host_write_file", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("write explode")))
    ok, err = await iface.set_mtu_persistent_netplan("eth0", 9000)
    assert ok is False
    assert "write explode" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_systemd_networkd_remaining_branches(monkeypatch):
    monkeypatch.setattr(iface, "_host_mkdir", lambda *_a, **_k: (True, None))

    # 456/461-462: empty content + read exception in scan loop.
    monkeypatch.setattr(
        iface,
        "_host_glob",
        lambda *_a, **_k: ["/etc/systemd/network/a.network", "/etc/systemd/network/b.network"],
    )
    read_calls = {"n": 0}

    def _read(path):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return None  # 456
        raise RuntimeError("scan fail")  # 461-462

    monkeypatch.setattr(iface, "_host_read_file", _read)
    monkeypatch.setattr(iface, "_host_write_file", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: _Result(returncode=0))
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth5", 9050)
    assert ok is True

    # 481-498: update existing [Link] section that lacks MTUBytes.
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/systemd/network/c.network"])
    monkeypatch.setattr(iface, "_host_read_file", lambda *_a, **_k: "[Match]\nName=eth0\n[Link]\n")
    captured = {}

    def _write(_path, content):
        captured["content"] = content
        return True, None

    monkeypatch.setattr(iface, "_host_write_file", _write)
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: _Result(returncode=0))
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9200)
    assert ok is True
    assert "MTUBytes=9200" in captured["content"]

    # 513-516: exception while writing/reloading
    monkeypatch.setattr(iface, "_host_write_file", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("write crash")))
    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9200)
    assert ok is False
    assert "write crash" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_systemd_networkd_replace_existing_mtubytes(monkeypatch):
    monkeypatch.setattr(iface, "_host_mkdir", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/systemd/network/eth0.network"])
    monkeypatch.setattr(
        iface,
        "_host_read_file",
        lambda *_a, **_k: "[Match]\nName=eth0\n[Link]\nMTUBytes=1500\n",
    )
    captured: dict[str, str] = {}

    def _write(_path, content):
        captured["content"] = content
        return True, None

    monkeypatch.setattr(iface, "_host_write_file", _write)
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: _Result(returncode=0))

    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9300)
    assert ok is True
    assert err is None
    assert "MTUBytes=9300" in captured["content"]
    assert "MTUBytes=1500" not in captured["content"]


@pytest.mark.asyncio
async def test_set_mtu_persistent_systemd_networkd_add_link_section(monkeypatch):
    monkeypatch.setattr(iface, "_host_mkdir", lambda *_a, **_k: (True, None))
    monkeypatch.setattr(iface, "_host_glob", lambda *_a, **_k: ["/etc/systemd/network/eth0.network"])
    monkeypatch.setattr(
        iface,
        "_host_read_file",
        lambda *_a, **_k: "[Match]\nName=eth0\n",
    )
    captured: dict[str, str] = {}

    def _write(_path, content):
        captured["content"] = content
        return True, None

    monkeypatch.setattr(iface, "_host_write_file", _write)
    monkeypatch.setattr(iface, "_run_on_host", lambda *_a, **_k: _Result(returncode=0))

    ok, err = await iface.set_mtu_persistent_systemd_networkd("eth0", 9400)
    assert ok is True
    assert err is None
    assert "[Link]" in captured["content"]
    assert "MTUBytes=9400" in captured["content"]


def test_get_interface_mtu_exception_and_virtual_path(monkeypatch):
    class _Path:
        def __init__(self, p: str):
            self.p = p

        def exists(self):
            if self.p.endswith("/mtu"):
                return True
            if self.p.startswith("/sys/devices/virtual/net/eth0"):
                return True
            if self.p.startswith("/sys/class/net/eth0/device"):
                return True
            return False

        def read_text(self):
            return "not-an-int"

    monkeypatch.setattr(iface, "Path", _Path)
    assert iface.get_interface_mtu("eth0") is None
    assert iface.is_physical_interface("eth0") is False
