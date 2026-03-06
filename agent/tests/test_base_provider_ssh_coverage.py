"""Tests for agent/providers/base.py — SSH command execution and VLAN persistence.

Covers:
- Provider._run_ssh_command() — success, failure, exception
- VlanPersistenceMixin._save_vlan_allocations() — write to file
- VlanPersistenceMixin._load_vlan_allocations() — read, missing, corrupted
- VlanPersistenceMixin._remove_vlan_file() — file exists, missing
- VlanPersistenceMixin.get_node_vlans() — lookup
- VlanPersistenceMixin._is_orphan_lab() — exact match, prefix match, orphan
- VlanPersistenceMixin._cleanup_orphan_vlans() — orphaned labs detected, no orphans
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.providers.base import (
    DeployResult,
    DestroyResult,
    NodeActionResult,
    NodeInfo,
    NodeStatus,
    Provider,
    StatusResult,
    VlanPersistenceMixin,
)


# ---------------------------------------------------------------------------
# Concrete provider for testing abstract class methods
# ---------------------------------------------------------------------------


class _ConcreteProvider(Provider):
    """Minimal concrete provider for testing base class methods."""

    @property
    def name(self) -> str:
        return "test"

    async def deploy(self, lab_id, topology, workspace):
        return DeployResult(success=True)

    async def destroy(self, lab_id, workspace):
        return DestroyResult(success=True)

    async def status(self, lab_id, workspace):
        return StatusResult(lab_exists=False)

    async def start_node(self, lab_id, node_name, workspace):
        return NodeActionResult(success=True, node_name=node_name)

    async def stop_node(self, lab_id, node_name, workspace):
        return NodeActionResult(success=True, node_name=node_name)


class _ConcreteVlanProvider(VlanPersistenceMixin):
    """Concrete class using VlanPersistenceMixin for testing."""

    def __init__(self):
        self.__init_vlan_state__()


# ---------------------------------------------------------------------------
# 1. Provider base class basics
# ---------------------------------------------------------------------------


class TestProviderBase:
    """Tests for Provider abstract base class defaults."""

    def test_display_name_default(self):
        p = _ConcreteProvider()
        assert p.display_name == "Test"

    def test_capabilities_default(self):
        p = _ConcreteProvider()
        assert "deploy" in p.capabilities
        assert "console" in p.capabilities

    @pytest.mark.asyncio
    async def test_create_node_not_implemented(self):
        p = _ConcreteProvider()
        with pytest.raises(NotImplementedError, match="test"):
            await p.create_node("lab1", "node1", "linux", Path("/tmp"))

    @pytest.mark.asyncio
    async def test_destroy_node_not_implemented(self):
        p = _ConcreteProvider()
        with pytest.raises(NotImplementedError, match="test"):
            await p.destroy_node("lab1", "node1", Path("/tmp"))

    @pytest.mark.asyncio
    async def test_get_console_command_returns_none(self):
        p = _ConcreteProvider()
        result = await p.get_console_command("lab1", "node1", Path("/tmp"))
        assert result is None

    @pytest.mark.asyncio
    async def test_discover_labs_returns_empty(self):
        p = _ConcreteProvider()
        result = await p.discover_labs()
        assert result == {}

    @pytest.mark.asyncio
    async def test_cleanup_orphan_resources_returns_empty(self):
        p = _ConcreteProvider()
        result = await p.cleanup_orphan_resources(set())
        assert result == {}


# ---------------------------------------------------------------------------
# 2. _run_ssh_command
# ---------------------------------------------------------------------------


class TestRunSshCommand:
    """Tests for Provider._run_ssh_command()."""

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self):
        """Successful SSH command returns stdout."""
        p = _ConcreteProvider()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"config output\n", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show running-config",
                context_label="router1",
            )

        assert result == "config output\n"

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_none(self):
        """Non-zero exit code returns None."""
        p = _ConcreteProvider()

        mock_proc = AsyncMock()
        mock_proc.returncode = 255
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Connection refused"))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show version",
                context_label="router1",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Exception during SSH command returns None."""
        p = _ConcreteProvider()

        with patch(
            "asyncio.create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("sshpass not found")),
        ):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show version",
                context_label="router1",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_stdout_returns_none(self):
        """Empty stdout returns None (stdout is falsy)."""
        p = _ConcreteProvider()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show version",
                context_label="router1",
            )

        # Empty bytes decodes to empty string, but `b""` is falsy so stdout check returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_none_stdout_returns_none(self):
        """None stdout returns None."""
        p = _ConcreteProvider()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(None, None))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show version",
                context_label="router1",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_nonzero_with_none_stderr(self):
        """Non-zero exit with None stderr should not crash."""
        p = _ConcreteProvider()

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", None))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await p._run_ssh_command(
                ip="10.0.0.1",
                user="admin",
                password="cisco",
                command="show version",
                context_label="router1",
            )

        assert result is None


# ---------------------------------------------------------------------------
# 3. VlanPersistenceMixin — _save_vlan_allocations
# ---------------------------------------------------------------------------


class TestSaveVlanAllocations:
    """Tests for VlanPersistenceMixin._save_vlan_allocations()."""

    def test_save_creates_file(self, tmp_path):
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["lab1"] = {"node1": [100, 101], "node2": [102]}
        provider._next_vlan["lab1"] = 103

        provider._save_vlan_allocations("lab1", tmp_path)

        vlan_file = tmp_path / "vlans" / "lab1.json"
        assert vlan_file.exists()

        data = json.loads(vlan_file.read_text())
        assert data["allocations"]["node1"] == [100, 101]
        assert data["next_vlan"] == 103

    def test_save_no_allocations(self, tmp_path):
        """Saving with no allocations still creates a file with defaults."""
        provider = _ConcreteVlanProvider()

        provider._save_vlan_allocations("lab1", tmp_path)

        vlan_file = tmp_path / "vlans" / "lab1.json"
        assert vlan_file.exists()

        data = json.loads(vlan_file.read_text())
        assert data["allocations"] == {}
        assert data["next_vlan"] == VlanPersistenceMixin.VLAN_RANGE_START

    def test_save_creates_vlans_directory(self, tmp_path):
        """The vlans directory is created if it doesn't exist."""
        provider = _ConcreteVlanProvider()
        workspace = tmp_path / "new_workspace"
        # Note: vlans_dir creates parents=True

        provider._save_vlan_allocations("lab1", workspace)

        assert (workspace / "vlans" / "lab1.json").exists()

    def test_save_handles_write_error(self, tmp_path):
        """Write errors are logged but don't raise."""
        provider = _ConcreteVlanProvider()

        # Make the vlans dir a file to cause a write error
        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        (vlans_dir / "lab1.json").mkdir()  # directory instead of file

        # Should not raise
        provider._save_vlan_allocations("lab1", tmp_path)


# ---------------------------------------------------------------------------
# 4. VlanPersistenceMixin — _load_vlan_allocations
# ---------------------------------------------------------------------------


class TestLoadVlanAllocations:
    """Tests for VlanPersistenceMixin._load_vlan_allocations()."""

    def test_load_success(self, tmp_path):
        provider = _ConcreteVlanProvider()

        # Write a valid VLAN file
        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        vlan_file = vlans_dir / "lab1.json"
        vlan_file.write_text(json.dumps({
            "allocations": {"node1": [100, 101]},
            "next_vlan": 102,
        }))

        result = provider._load_vlan_allocations("lab1", tmp_path)

        assert result is True
        assert provider._vlan_allocations["lab1"] == {"node1": [100, 101]}
        assert provider._next_vlan["lab1"] == 102

    def test_load_missing_file(self, tmp_path):
        provider = _ConcreteVlanProvider()
        result = provider._load_vlan_allocations("nonexistent", tmp_path)
        assert result is False

    def test_load_corrupted_json(self, tmp_path):
        provider = _ConcreteVlanProvider()

        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        vlan_file = vlans_dir / "lab1.json"
        vlan_file.write_text("not valid json{{{")

        result = provider._load_vlan_allocations("lab1", tmp_path)
        assert result is False

    def test_load_missing_fields_uses_defaults(self, tmp_path):
        """Missing fields should use defaults."""
        provider = _ConcreteVlanProvider()

        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        vlan_file = vlans_dir / "lab1.json"
        vlan_file.write_text(json.dumps({}))

        result = provider._load_vlan_allocations("lab1", tmp_path)

        assert result is True
        assert provider._vlan_allocations["lab1"] == {}
        assert provider._next_vlan["lab1"] == VlanPersistenceMixin.VLAN_RANGE_START


# ---------------------------------------------------------------------------
# 5. VlanPersistenceMixin — _remove_vlan_file
# ---------------------------------------------------------------------------


class TestRemoveVlanFile:
    """Tests for VlanPersistenceMixin._remove_vlan_file()."""

    def test_remove_existing_file(self, tmp_path):
        provider = _ConcreteVlanProvider()

        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        vlan_file = vlans_dir / "lab1.json"
        vlan_file.write_text("{}")

        provider._remove_vlan_file("lab1", tmp_path)

        assert not vlan_file.exists()

    def test_remove_nonexistent_file(self, tmp_path):
        """Removing a non-existent file is a no-op."""
        provider = _ConcreteVlanProvider()

        # Should not raise
        provider._remove_vlan_file("nonexistent", tmp_path)

    def test_remove_with_unlink_error(self, tmp_path):
        """Unlink errors are logged but don't raise."""
        provider = _ConcreteVlanProvider()

        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        vlan_file = vlans_dir / "lab1.json"
        vlan_file.mkdir()  # directory instead of file — unlink will fail

        # Should not raise
        provider._remove_vlan_file("lab1", tmp_path)


# ---------------------------------------------------------------------------
# 6. VlanPersistenceMixin — get_node_vlans
# ---------------------------------------------------------------------------


class TestGetNodeVlans:
    """Tests for VlanPersistenceMixin.get_node_vlans()."""

    def test_existing_node(self):
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["lab1"] = {"node1": [100, 101]}

        result = provider.get_node_vlans("lab1", "node1")
        assert result == [100, 101]

    def test_missing_lab(self):
        provider = _ConcreteVlanProvider()
        result = provider.get_node_vlans("nonexistent", "node1")
        assert result == []

    def test_missing_node(self):
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["lab1"] = {"node1": [100]}
        result = provider.get_node_vlans("lab1", "nonexistent")
        assert result == []


# ---------------------------------------------------------------------------
# 7. VlanPersistenceMixin — _is_orphan_lab (static)
# ---------------------------------------------------------------------------


class TestIsOrphanLab:
    """Tests for VlanPersistenceMixin._is_orphan_lab()."""

    def test_exact_match_not_orphan(self):
        assert VlanPersistenceMixin._is_orphan_lab("lab-123", {"lab-123", "lab-456"}) is False

    def test_no_match_is_orphan(self):
        assert VlanPersistenceMixin._is_orphan_lab("lab-789", {"lab-123", "lab-456"}) is True

    def test_truncated_id_prefix_match(self):
        """Short lab IDs that are prefixes of valid IDs are not orphans."""
        full_id = "abcdef1234567890abcdef1234567890"
        short_id = "abcdef12345"  # < 20 chars
        assert VlanPersistenceMixin._is_orphan_lab(short_id, {full_id}) is False

    def test_truncated_id_no_prefix_match(self):
        short_id = "xyz123"
        assert VlanPersistenceMixin._is_orphan_lab(short_id, {"abcdef1234567890"}) is True

    def test_long_id_exact_only(self):
        """IDs >= 20 chars only match exactly."""
        long_id = "a" * 25
        valid_ids = {"b" * 25}
        assert VlanPersistenceMixin._is_orphan_lab(long_id, valid_ids) is True

    def test_empty_valid_set(self):
        assert VlanPersistenceMixin._is_orphan_lab("lab-1", set()) is True


# ---------------------------------------------------------------------------
# 8. VlanPersistenceMixin — _cleanup_orphan_vlans
# ---------------------------------------------------------------------------


class TestCleanupOrphanVlans:
    """Tests for VlanPersistenceMixin._cleanup_orphan_vlans()."""

    def test_cleanup_removes_allocations(self, tmp_path):
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["orphan-lab"] = {"node1": [100]}
        provider._next_vlan["orphan-lab"] = 101

        # Write a VLAN file
        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        (vlans_dir / "orphan-lab.json").write_text("{}")

        provider._cleanup_orphan_vlans("orphan-lab", tmp_path)

        assert "orphan-lab" not in provider._vlan_allocations
        assert "orphan-lab" not in provider._next_vlan
        assert not (vlans_dir / "orphan-lab.json").exists()

    def test_cleanup_no_allocations(self, tmp_path):
        """Cleanup of a lab with no allocations is a no-op."""
        provider = _ConcreteVlanProvider()

        # Should not raise
        provider._cleanup_orphan_vlans("nonexistent", tmp_path)

    def test_cleanup_none_workspace(self):
        """Cleanup with None workspace skips file removal."""
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["lab1"] = {"node1": [100]}
        provider._next_vlan["lab1"] = 101

        provider._cleanup_orphan_vlans("lab1", None)

        assert "lab1" not in provider._vlan_allocations
        assert "lab1" not in provider._next_vlan

    def test_cleanup_workspace_not_exists(self, tmp_path):
        """Cleanup with a workspace that doesn't exist skips file removal."""
        provider = _ConcreteVlanProvider()
        provider._vlan_allocations["lab1"] = {"node1": [100]}

        nonexistent = tmp_path / "does_not_exist"
        provider._cleanup_orphan_vlans("lab1", nonexistent)

        assert "lab1" not in provider._vlan_allocations


# ---------------------------------------------------------------------------
# 9. VlanPersistenceMixin — roundtrip save/load
# ---------------------------------------------------------------------------


class TestVlanRoundtrip:
    """Integration test: save then load VLAN allocations."""

    def test_roundtrip(self, tmp_path):
        saver = _ConcreteVlanProvider()
        saver._vlan_allocations["lab1"] = {"r1": [100, 101], "r2": [102, 103]}
        saver._next_vlan["lab1"] = 200

        saver._save_vlan_allocations("lab1", tmp_path)

        loader = _ConcreteVlanProvider()
        result = loader._load_vlan_allocations("lab1", tmp_path)

        assert result is True
        assert loader._vlan_allocations["lab1"] == {"r1": [100, 101], "r2": [102, 103]}
        assert loader._next_vlan["lab1"] == 200


# ---------------------------------------------------------------------------
# 10. NodeStatus enum and data classes
# ---------------------------------------------------------------------------


class TestDataClasses:
    """Quick sanity checks for data classes and enums."""

    def test_node_status_values(self):
        assert NodeStatus.RUNNING == "running"
        assert NodeStatus.ERROR == "error"

    def test_node_info_defaults(self):
        info = NodeInfo(name="r1", status=NodeStatus.RUNNING)
        assert info.name == "r1"
        assert info.ip_addresses == []
        assert info.interfaces == {}
        assert info.error is None

    def test_deploy_result(self):
        result = DeployResult(success=True, stdout="done")
        assert result.success is True
        assert result.nodes == []

    def test_destroy_result(self):
        result = DestroyResult(success=False, error="timeout")
        assert result.error == "timeout"

    def test_status_result(self):
        result = StatusResult(lab_exists=True)
        assert result.nodes == []

    def test_node_action_result(self):
        result = NodeActionResult(success=True, node_name="r1", new_status=NodeStatus.RUNNING)
        assert result.new_status == NodeStatus.RUNNING
