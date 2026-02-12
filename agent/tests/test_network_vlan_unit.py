from __future__ import annotations

from agent.network import vlan as vlan_mod


def test_create_vlan_interface_rejects_invalid_id():
    mgr = vlan_mod.VlanManager()
    assert mgr.create_vlan_interface("eth0", 0, "lab1") is None
    assert mgr.create_vlan_interface("eth0", 4095, "lab1") is None


def test_cleanup_lab_removes_tracked_interfaces(monkeypatch):
    mgr = vlan_mod.VlanManager()
    mgr._interfaces_by_lab["lab1"] = {"eth0.100", "eth0.200"}

    deleted = []
    monkeypatch.setattr(mgr, "delete_vlan_interface", lambda name: deleted.append(name) or True)

    removed = mgr.cleanup_lab("lab1")
    assert sorted(removed) == sorted(["eth0.100", "eth0.200"])
    assert sorted(deleted) == sorted(["eth0.100", "eth0.200"])

