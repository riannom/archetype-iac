from __future__ import annotations

from agent.network.vlan import VlanManager


def test_create_vlan_interface_tracks_existing() -> None:
    manager = VlanManager()

    def fake_interface_exists(name: str) -> bool:
        return name == "eth0.100"

    manager.interface_exists = fake_interface_exists  # type: ignore[assignment]

    iface = manager.create_vlan_interface("eth0", 100, "lab-1")
    assert iface == "eth0.100"
    assert "eth0.100" in manager.get_lab_interfaces("lab-1")


def test_create_vlan_interface_invalid_vlan() -> None:
    manager = VlanManager()
    assert manager.create_vlan_interface("eth0", 5000, "lab-1") is None


def test_delete_vlan_interface_missing_is_ok() -> None:
    manager = VlanManager()
    manager.interface_exists = lambda name: False  # type: ignore[assignment]
    assert manager.delete_vlan_interface("eth0.200") is True


def test_cleanup_lab_removes_interfaces() -> None:
    manager = VlanManager()
    manager.interface_exists = lambda name: True  # type: ignore[assignment]
    manager._run_ip_command = lambda args: (0, "", "")  # type: ignore[assignment]

    manager._interfaces_by_lab["lab-1"] = {"eth0.100", "eth0.200"}
    deleted = manager.cleanup_lab("lab-1")

    assert sorted(deleted) == ["eth0.100", "eth0.200"]
    assert manager.get_lab_interfaces("lab-1") == set()
