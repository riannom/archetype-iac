from __future__ import annotations

import types

import agent.network.interface_config as iface


def test_host_glob_container_mode(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)

    def fake_run(cmd, timeout=5):
        assert cmd[:3] == ["find", "/etc/netplan", "-maxdepth"]
        return types.SimpleNamespace(returncode=0, stdout="/etc/netplan/00.yaml\n", stderr="")

    monkeypatch.setattr(iface, "_run_on_host", fake_run)
    assert iface._host_glob("/etc/netplan", "*.yaml") == ["/etc/netplan/00.yaml"]


def test_host_read_file_container_mode(monkeypatch):
    monkeypatch.setattr(iface, "_is_in_container", lambda: True)
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=0, stdout="data", stderr=""))

    assert iface._host_read_file("/etc/hosts") == "data"
