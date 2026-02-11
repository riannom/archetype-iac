from __future__ import annotations

import types

import agent.network.interface_config as iface


def test_host_write_file_container_mode(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)

    class Result:
        def __init__(self, returncode=0, stderr=""):
            self.returncode = returncode
            self.stderr = stderr

    monkeypatch.setattr(iface.subprocess, "run", lambda *_args, **_kwargs: Result(0))

    ok, err = iface._host_write_file("/etc/test", "data")
    assert ok is True
    assert err is None


def test_host_mkdir_container_mode(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=0, stderr=""))

    ok, err = iface._host_mkdir("/etc/test")
    assert ok is True
    assert err is None
