"""Extended tests for agent/network/vlan.py — VlanManager and module functions.

Complements test_vlan_manager.py with additional edge cases:
- create_vlan_interface: parent not found, create failure, bring-up failure
- delete_vlan_interface: ip command failure
- cleanup_lab: unknown lab_id
- _run_ip_command_async
- cleanup_external_networks module-level function
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from agent.network.vlan import (
    VlanManager,
    VlanInterface,
    cleanup_external_networks,
    get_vlan_manager,
)


# ---------------------------------------------------------------------------
# VlanInterface dataclass
# ---------------------------------------------------------------------------


def test_vlan_interface_name() -> None:
    vi = VlanInterface(parent="ens192", vlan_id=200, lab_id="lab-1")
    assert vi.name == "ens192.200"


# ---------------------------------------------------------------------------
# create_vlan_interface — additional branches
# ---------------------------------------------------------------------------


def test_create_vlan_interface_parent_missing() -> None:
    """If parent interface doesn't exist, return None."""
    manager = VlanManager()
    manager.interface_exists = lambda name: False  # type: ignore[assignment]

    result = manager.create_vlan_interface("eth0", 100, "lab-1")
    assert result is None


def test_create_vlan_interface_create_command_fails() -> None:
    """ip link add failure returns None."""
    manager = VlanManager()

    call_count = 0

    def fake_exists(name: str) -> bool:
        return name == "eth0"  # parent exists, vlan iface does not

    def fake_run(args: list[str]) -> tuple[int, str, str]:
        nonlocal call_count
        call_count += 1
        if "add" in args:
            return (1, "", "RTNETLINK answers: Operation not permitted")
        # interface_exists for parent
        return (0, "", "")

    manager.interface_exists = fake_exists  # type: ignore[assignment]
    manager._run_ip_command = fake_run  # type: ignore[assignment]

    result = manager.create_vlan_interface("eth0", 100, "lab-1")
    assert result is None


def test_create_vlan_interface_bring_up_fails() -> None:
    """If 'ip link set up' fails, interface is deleted and None returned."""
    manager = VlanManager()

    def fake_exists(name: str) -> bool:
        return name == "eth0"

    commands_run: list[list[str]] = []

    def fake_run(args: list[str]) -> tuple[int, str, str]:
        commands_run.append(args)
        if "set" in args and "up" in args:
            return (1, "", "Cannot bring up")
        return (0, "", "")

    manager.interface_exists = fake_exists  # type: ignore[assignment]
    manager._run_ip_command = fake_run  # type: ignore[assignment]

    result = manager.create_vlan_interface("eth0", 100, "lab-1")
    assert result is None

    # Verify cleanup was attempted
    delete_cmds = [c for c in commands_run if "delete" in c]
    assert len(delete_cmds) == 1
    assert "eth0.100" in delete_cmds[0]


def test_create_vlan_interface_success() -> None:
    """Full success path: create + bring up."""
    manager = VlanManager()

    def fake_exists(name: str) -> bool:
        return name == "eth0"

    manager.interface_exists = fake_exists  # type: ignore[assignment]
    manager._run_ip_command = lambda args: (0, "", "")  # type: ignore[assignment]

    result = manager.create_vlan_interface("eth0", 100, "lab-1")
    assert result == "eth0.100"
    assert "eth0.100" in manager.get_lab_interfaces("lab-1")


def test_create_vlan_interface_invalid_vlan_zero() -> None:
    """VLAN ID 0 is invalid."""
    manager = VlanManager()
    assert manager.create_vlan_interface("eth0", 0, "lab-1") is None


def test_create_vlan_interface_invalid_vlan_negative() -> None:
    """Negative VLAN ID is invalid."""
    manager = VlanManager()
    assert manager.create_vlan_interface("eth0", -1, "lab-1") is None


# ---------------------------------------------------------------------------
# delete_vlan_interface — ip command failure
# ---------------------------------------------------------------------------


def test_delete_vlan_interface_command_fails() -> None:
    """ip link delete failure returns False."""
    manager = VlanManager()
    manager.interface_exists = lambda name: True  # type: ignore[assignment]
    manager._run_ip_command = lambda args: (1, "", "Device busy")  # type: ignore[assignment]

    result = manager.delete_vlan_interface("eth0.100")
    assert result is False


def test_delete_vlan_interface_success_removes_from_tracking() -> None:
    """Successful delete removes interface from all lab tracking."""
    manager = VlanManager()
    manager._interfaces_by_lab["lab-1"] = {"eth0.100", "eth0.200"}
    manager._interfaces_by_lab["lab-2"] = {"eth0.100"}

    manager.interface_exists = lambda name: True  # type: ignore[assignment]
    manager._run_ip_command = lambda args: (0, "", "")  # type: ignore[assignment]

    result = manager.delete_vlan_interface("eth0.100")
    assert result is True
    assert "eth0.100" not in manager._interfaces_by_lab["lab-1"]
    assert "eth0.100" not in manager._interfaces_by_lab["lab-2"]


# ---------------------------------------------------------------------------
# cleanup_lab
# ---------------------------------------------------------------------------


def test_cleanup_lab_unknown_lab_id() -> None:
    """Unknown lab_id returns empty list."""
    manager = VlanManager()
    result = manager.cleanup_lab("nonexistent")
    assert result == []


def test_cleanup_lab_partial_failure() -> None:
    """If some interfaces fail to delete, only successful ones are returned."""
    manager = VlanManager()
    manager._interfaces_by_lab["lab-1"] = {"eth0.100", "eth0.200"}

    def fake_exists(name: str) -> bool:
        return True

    def fake_run(args: list[str]) -> tuple[int, str, str]:
        # Fail for eth0.200
        if "eth0.200" in args:
            return (1, "", "error")
        return (0, "", "")

    manager.interface_exists = fake_exists  # type: ignore[assignment]
    manager._run_ip_command = fake_run  # type: ignore[assignment]

    result = manager.cleanup_lab("lab-1")
    assert "eth0.100" in result
    assert "eth0.200" not in result


# ---------------------------------------------------------------------------
# _run_ip_command — subprocess edge cases
# ---------------------------------------------------------------------------


def test_run_ip_command_timeout() -> None:
    """Subprocess timeout returns error tuple."""
    manager = VlanManager()

    import subprocess

    with patch(
        "agent.network.vlan.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ip", timeout=30),
    ):
        rc, stdout, stderr = manager._run_ip_command(["link", "show", "eth0"])
        assert rc == 1
        assert "timed out" in stderr.lower()


def test_run_ip_command_generic_exception() -> None:
    """Generic exception returns error tuple."""
    manager = VlanManager()

    with patch(
        "agent.network.vlan.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        rc, stdout, stderr = manager._run_ip_command(["link", "show", "eth0"])
        assert rc == 1
        assert "permission denied" in stderr.lower()


# ---------------------------------------------------------------------------
# _run_ip_command_async
# ---------------------------------------------------------------------------


def test_run_ip_command_async() -> None:
    """Async wrapper delegates to sync method."""
    manager = VlanManager()
    manager._run_ip_command = lambda args: (0, "output", "")  # type: ignore[assignment]

    rc, stdout, stderr = asyncio.run(manager._run_ip_command_async(["link", "show"]))
    assert rc == 0
    assert stdout == "output"


# ---------------------------------------------------------------------------
# list_all_interfaces / get_lab_interfaces
# ---------------------------------------------------------------------------


def test_list_all_interfaces() -> None:
    manager = VlanManager()
    manager._interfaces_by_lab["lab-1"] = {"eth0.100"}
    manager._interfaces_by_lab["lab-2"] = {"eth0.200", "eth0.300"}

    result = manager.list_all_interfaces()
    assert result == {"lab-1": {"eth0.100"}, "lab-2": {"eth0.200", "eth0.300"}}
    # Verify copies (not references)
    result["lab-1"].add("eth0.999")
    assert "eth0.999" not in manager._interfaces_by_lab["lab-1"]


def test_get_lab_interfaces_empty() -> None:
    manager = VlanManager()
    assert manager.get_lab_interfaces("nonexistent") == set()


# ---------------------------------------------------------------------------
# get_vlan_manager() singleton
# ---------------------------------------------------------------------------


def test_get_vlan_manager_singleton() -> None:
    import agent.network.vlan as vlan_mod

    vlan_mod._vlan_manager = None
    m1 = get_vlan_manager()
    m2 = get_vlan_manager()
    assert m1 is m2
    vlan_mod._vlan_manager = None  # cleanup


# ---------------------------------------------------------------------------
# cleanup_external_networks() module-level async function
# ---------------------------------------------------------------------------


def test_cleanup_external_networks() -> None:
    """Module-level async cleanup delegates to VlanManager.cleanup_lab."""
    import agent.network.vlan as vlan_mod

    mock_manager = MagicMock()
    mock_manager.cleanup_lab.return_value = ["eth0.100"]

    vlan_mod._vlan_manager = mock_manager

    result = asyncio.run(cleanup_external_networks("lab-1"))
    assert result == ["eth0.100"]
    mock_manager.cleanup_lab.assert_called_once_with("lab-1")

    vlan_mod._vlan_manager = None  # cleanup
