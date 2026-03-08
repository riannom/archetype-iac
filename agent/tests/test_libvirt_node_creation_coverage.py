"""Tests for LibvirtProvider node creation paths.

Covers _coalesce, _create_node_pre_sync, _define_domain_sync,
and the create_node orchestration method.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure agent package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.providers.libvirt as libvirt_mod
from agent.providers.base import NodeActionResult, NodeStatus


# ---------------------------------------------------------------------------
# Helper: create a LibvirtProvider without real libvirt connection
# ---------------------------------------------------------------------------

def _make_provider() -> libvirt_mod.LibvirtProvider:
    p = libvirt_mod.LibvirtProvider.__new__(libvirt_mod.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._n9kv_loader_recovery_attempts = {}
    p._n9kv_loader_recovery_last_at = {}
    p._n9kv_poap_skip_attempted = set()
    p._n9kv_admin_password_completed = set()
    p._n9kv_panic_recovery_attempts = {}
    p._n9kv_panic_recovery_last_at = {}
    p._n9kv_panic_last_log_size = {}
    p._conn = MagicMock()
    p._uri = "qemu:///system"
    p._vm_port_cache = {}
    return p


# ---------------------------------------------------------------------------
# _coalesce tests
# ---------------------------------------------------------------------------

class TestCoalesce:
    def test_returns_value_when_not_none(self):
        assert libvirt_mod._coalesce("hello", "default") == "hello"

    def test_returns_default_when_none(self):
        assert libvirt_mod._coalesce(None, "default") == "default"

    def test_preserves_falsy_non_none_values(self):
        """Zero, empty string, False are not None — should be returned."""
        assert libvirt_mod._coalesce(0, 99) == 0
        assert libvirt_mod._coalesce("", "fallback") == ""
        assert libvirt_mod._coalesce(False, True) is False


# ---------------------------------------------------------------------------
# _create_node_pre_sync tests
# ---------------------------------------------------------------------------

class TestCreateNodePreSync:
    def test_already_running_returns_result(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(True, "abc123", NodeStatus.RUNNING, "expected"),
        )
        provider._running_domain_identity_visible = MagicMock(return_value=True)
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
        )

        assert result is not None
        assert isinstance(result, NodeActionResult)
        assert result.success is True
        assert result.node_name == "r1"
        assert result.new_status == NodeStatus.RUNNING
        assert "already running" in result.stdout

    def test_already_running_without_metadata_visibility_returns_error(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(True, "abc123", NodeStatus.RUNNING, "expected"),
        )
        provider._running_domain_identity_visible = MagicMock(return_value=False)
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
            node_definition_id="node-def-1",
        )

        assert result is not None
        assert result.success is False
        assert "metadata-backed status" in (result.error or "")

    def test_not_running_returns_none(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(False, None, None, None),
        )
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
        )

        assert result is None

    def test_existing_stopped_expected_domain_returns_stopped_result(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(False, "abc123", NodeStatus.STOPPED, "expected"),
        )
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
            node_definition_id="node-def-1",
        )

        assert result is not None
        assert result.success is True
        assert result.new_status == NodeStatus.STOPPED
        assert "already exists" in (result.stdout or "")

    def test_existing_stopped_foreign_domain_returns_error(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(False, "abc123", NodeStatus.STOPPED, "foreign"),
        )
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
        )

        assert result is not None
        assert result.success is False
        assert "not managed by Archetype" in (result.error or "")

    def test_existing_stopped_stale_managed_domain_returns_error(self):
        provider = _make_provider()
        provider._node_precheck_sync = MagicMock(
            return_value=(False, "abc123", NodeStatus.STOPPED, "stale_managed"),
        )
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))

        result = provider._create_node_pre_sync(
            "lab1", "r1", "arch-lab1-r1", Path("/tmp/ws"),
            node_definition_id="node-def-1",
        )

        assert result is not None
        assert result.success is False
        assert "different node identity" in (result.error or "")

    @pytest.mark.asyncio
    async def test_probe_runtime_conflict_reports_stale_managed_domain(self):
        provider = _make_provider()
        provider._run_libvirt = AsyncMock(
            return_value=(False, "abc123", NodeStatus.STOPPED, "stale_managed"),
        )

        result = await provider.probe_runtime_conflict(
            "lab1",
            "r1",
            node_definition_id="node-def-1",
        )

        assert result.available is False
        assert result.classification == "stale_managed"
        assert result.status == NodeStatus.STOPPED.value
        assert "different managed node identity" in (result.error or "")


# ---------------------------------------------------------------------------
# _define_domain_sync tests
# ---------------------------------------------------------------------------

class TestDefineDomainSync:
    def test_define_success_returns_true(self):
        provider = _make_provider()
        mock_domain = MagicMock()
        provider._conn.defineXML.return_value = mock_domain

        result = provider._define_domain_sync("arch-lab1-r1", "<domain/>")

        assert result is True
        provider._conn.defineXML.assert_called_once_with("<domain/>")

    def test_define_returns_none_yields_false(self):
        provider = _make_provider()
        provider._conn.defineXML.return_value = None

        result = provider._define_domain_sync("arch-lab1-r1", "<domain/>")

        assert result is False


# ---------------------------------------------------------------------------
# create_node orchestration tests
# ---------------------------------------------------------------------------

class TestCreateNode:
    """Tests for the async create_node method."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _setup_provider(self):
        provider = _make_provider()
        # _run_libvirt just calls the function directly for testing
        provider._run_libvirt = AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
        provider._domain_name = MagicMock(return_value="arch-lab1-r1")
        provider._disks_dir = MagicMock(return_value=Path("/tmp/disks"))
        provider._allocate_vlans = MagicMock(return_value=[100, 101])
        provider._generate_domain_xml = MagicMock(return_value="<domain/>")
        provider._canonical_kind = MagicMock(return_value="generic")
        return provider

    @patch("agent.providers.libvirt.get_libvirt_config")
    @patch("agent.providers.libvirt.get_vendor_config")
    def test_early_return_when_already_running(self, mock_vendor, mock_libvirt_config):
        provider = self._setup_provider()
        early_result = NodeActionResult(
            success=True, node_name="r1", new_status=NodeStatus.RUNNING,
            stdout="Domain arch-lab1-r1 already running",
        )
        # _run_libvirt returns early_result for _create_node_pre_sync
        provider._run_libvirt = AsyncMock(return_value=early_result)

        result = self._run(provider.create_node(
            "lab1", "r1", "generic", Path("/tmp/ws"),
        ))

        assert result.success is True
        assert result.new_status == NodeStatus.RUNNING

    @patch("agent.providers.libvirt.get_libvirt_config")
    @patch("agent.providers.libvirt.get_vendor_config")
    def test_no_base_image_returns_failure(self, mock_vendor, mock_libvirt_config):
        provider = self._setup_provider()
        mock_libvirt_config.return_value = SimpleNamespace(
            memory_mb=2048, cpu_count=2, machine_type="pc",
            disk_driver="virtio", nic_driver="virtio",
            readiness_probe=None, readiness_pattern=None, readiness_timeout=300,
            efi_boot=False, efi_vars="", serial_type="pty", nographic=True,
            serial_port_count=1, smbios_product=None, reserved_nics=0,
            cpu_sockets=0, needs_nested_vmx=False, data_volume_gb=0,
            config_inject_method=None, config_inject_partition=None,
            config_inject_fs_type=None, config_inject_path=None,
            config_inject_iso_volume_label=None, config_inject_iso_filename=None,
        )
        provider._create_node_pre_sync = MagicMock(return_value=None)
        provider._get_base_image = MagicMock(return_value=None)

        result = self._run(provider.create_node(
            "lab1", "r1", "generic", Path("/tmp/ws"),
        ))

        assert result.success is False
        assert "No base image" in result.error

    @patch("agent.providers.libvirt.get_libvirt_config")
    @patch("agent.providers.libvirt.get_vendor_config")
    def test_define_failure_returns_error(self, mock_vendor, mock_libvirt_config):
        provider = self._setup_provider()
        mock_libvirt_config.return_value = SimpleNamespace(
            memory_mb=2048, cpu_count=2, machine_type="pc",
            disk_driver="virtio", nic_driver="virtio",
            readiness_probe=None, readiness_pattern=None, readiness_timeout=300,
            efi_boot=False, efi_vars="", serial_type="pty", nographic=True,
            serial_port_count=1, smbios_product=None, reserved_nics=0,
            cpu_sockets=0, needs_nested_vmx=False, data_volume_gb=0,
            config_inject_method=None, config_inject_partition=None,
            config_inject_fs_type=None, config_inject_path=None,
            config_inject_iso_volume_label=None, config_inject_iso_filename=None,
        )
        mock_vendor.return_value = None
        provider._create_node_pre_sync = MagicMock(return_value=None)
        provider._get_base_image = MagicMock(return_value="/images/test.qcow2")
        provider._verify_backing_image = MagicMock()
        provider._create_overlay_disk = AsyncMock(return_value=True)
        provider._resolve_management_network = MagicMock(return_value=(False, None))

        # Make _define_domain_sync return False
        provider._define_domain_sync = MagicMock(return_value=False)

        # Need _run_libvirt to dispatch correctly
        async def smart_run_libvirt(fn, *a, **kw):
            return fn(*a, **kw)
        provider._run_libvirt = smart_run_libvirt

        result = self._run(provider.create_node(
            "lab1", "r1", "generic", Path("/tmp/ws"),
            image="test.qcow2",
        ))

        assert result.success is False
        assert "Failed to define domain" in result.error

    @patch("agent.providers.libvirt.get_libvirt_config")
    @patch("agent.providers.libvirt.get_vendor_config")
    def test_successful_create_returns_stopped(self, mock_vendor, mock_libvirt_config):
        provider = self._setup_provider()
        mock_libvirt_config.return_value = SimpleNamespace(
            memory_mb=2048, cpu_count=2, machine_type="pc",
            disk_driver="virtio", nic_driver="virtio",
            readiness_probe=None, readiness_pattern=None, readiness_timeout=300,
            efi_boot=False, efi_vars="", serial_type="pty", nographic=True,
            serial_port_count=1, smbios_product=None, reserved_nics=0,
            cpu_sockets=0, needs_nested_vmx=False, data_volume_gb=0,
            config_inject_method=None, config_inject_partition=None,
            config_inject_fs_type=None, config_inject_path=None,
            config_inject_iso_volume_label=None, config_inject_iso_filename=None,
        )
        mock_vendor.return_value = None
        provider._create_node_pre_sync = MagicMock(return_value=None)
        provider._get_base_image = MagicMock(return_value="/images/test.qcow2")
        provider._verify_backing_image = MagicMock()
        provider._create_overlay_disk = AsyncMock(return_value=True)
        provider._resolve_management_network = MagicMock(return_value=(False, None))
        provider._define_domain_sync = MagicMock(return_value=True)

        async def smart_run_libvirt(fn, *a, **kw):
            return fn(*a, **kw)
        provider._run_libvirt = smart_run_libvirt

        result = self._run(provider.create_node(
            "lab1", "r1", "generic", Path("/tmp/ws"),
            image="test.qcow2",
        ))

        assert result.success is True
        assert result.new_status == NodeStatus.STOPPED
        assert "Defined domain" in result.stdout

    @patch("agent.providers.libvirt.get_libvirt_config")
    @patch("agent.providers.libvirt.get_vendor_config")
    def test_overlay_disk_failure_returns_error(self, mock_vendor, mock_libvirt_config):
        provider = self._setup_provider()
        mock_libvirt_config.return_value = SimpleNamespace(
            memory_mb=2048, cpu_count=2, machine_type="pc",
            disk_driver="virtio", nic_driver="virtio",
            readiness_probe=None, readiness_pattern=None, readiness_timeout=300,
            efi_boot=False, efi_vars="", serial_type="pty", nographic=True,
            serial_port_count=1, smbios_product=None, reserved_nics=0,
            cpu_sockets=0, needs_nested_vmx=False, data_volume_gb=0,
            config_inject_method=None, config_inject_partition=None,
            config_inject_fs_type=None, config_inject_path=None,
            config_inject_iso_volume_label=None, config_inject_iso_filename=None,
        )
        provider._create_node_pre_sync = MagicMock(return_value=None)
        provider._get_base_image = MagicMock(return_value="/images/test.qcow2")
        provider._verify_backing_image = MagicMock()
        provider._create_overlay_disk = AsyncMock(return_value=False)

        async def smart_run_libvirt(fn, *a, **kw):
            return fn(*a, **kw)
        provider._run_libvirt = smart_run_libvirt

        result = self._run(provider.create_node(
            "lab1", "r1", "generic", Path("/tmp/ws"),
            image="test.qcow2",
        ))

        assert result.success is False
        assert "Failed to create overlay disk" in result.error
