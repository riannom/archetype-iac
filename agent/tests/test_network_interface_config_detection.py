from __future__ import annotations

import types

import agent.network.interface_config as iface


def test_detect_network_manager_networkmanager(monkeypatch):
    def fake_run(cmd, timeout=5):
        if cmd[:2] == ["nmcli", "-t"]:
            return types.SimpleNamespace(returncode=0, stdout="running", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(iface, "_run_on_host", fake_run)
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])

    assert iface.detect_network_manager() == "networkmanager"


def test_detect_network_manager_netplan(monkeypatch):
    def fake_run(cmd, timeout=5):
        if cmd[0] == "nmcli":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[0] == "which":
            return types.SimpleNamespace(returncode=0, stdout="/usr/sbin/netplan", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(iface, "_run_on_host", fake_run)
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: ["/etc/netplan/00.yaml"])

    assert iface.detect_network_manager() == "netplan"


def test_detect_network_manager_systemd_networkd(monkeypatch):
    def fake_run(cmd, timeout=5):
        if cmd[0] == "nmcli":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[0] == "systemctl":
            return types.SimpleNamespace(returncode=0, stdout="active", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(iface, "_run_on_host", fake_run)
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])

    assert iface.detect_network_manager() == "systemd-networkd"


def test_detect_network_manager_unknown(monkeypatch):
    monkeypatch.setattr(iface, "_run_on_host", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(iface, "_host_glob", lambda *_args, **_kwargs: [])

    assert iface.detect_network_manager() == "unknown"
