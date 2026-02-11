from __future__ import annotations

import types

import pytest

import agent.network.interface_config as iface


def test_is_in_container_with_env(monkeypatch):
    monkeypatch.setenv("container", "1")
    assert iface._is_in_container() is True


def test_host_path_exists_calls_nsenter_when_container(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)

    class Result:
        def __init__(self, returncode: int = 0):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: Result(0))
    assert iface._host_path_exists("/etc/hosts") is True


@pytest.mark.asyncio
async def test_set_mtu_runtime_success(monkeypatch):
    class Result:
        def __init__(self, returncode: int = 0, stderr: str = ""):
            self.returncode = returncode
            self.stderr = stderr

    monkeypatch.setattr(iface.subprocess, "run", lambda *_args, **_kwargs: Result(0))

    ok, err = await iface.set_mtu_runtime("eth0", 1500)
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_set_mtu_runtime_failure(monkeypatch):
    class Result:
        def __init__(self, returncode: int = 1, stderr: str = "fail"):
            self.returncode = returncode
            self.stderr = stderr

    monkeypatch.setattr(iface.subprocess, "run", lambda *_args, **_kwargs: Result(1, "fail"))

    ok, err = await iface.set_mtu_runtime("eth0", 1500)
    assert ok is False
    assert "fail" in (err or "")


@pytest.mark.asyncio
async def test_set_mtu_persistent_networkmanager_no_connection(monkeypatch):
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""))

    ok, err = await iface.set_mtu_persistent_networkmanager("eth0", 1400)
    assert ok is False
    assert "No NetworkManager connection" in (err or "")

