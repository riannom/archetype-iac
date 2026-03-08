"""Tests for VLAN I/O paths in agent/providers/base.py.

Complements test_base_provider_ssh_coverage.py with additional edge-case
coverage for VlanPersistenceMixin methods:
- _save_vlan_allocations() — overwrite existing file, concurrent labs
- _load_vlan_allocations() — valid JSON but wrong structure types
- _remove_vlan_file() — vlans directory does not exist yet
- _cleanup_orphan_vlans() — only allocations (no next_vlan), workspace exists but no vlans dir
- cleanup_orphan_resources() — base implementation returns empty dict
- _is_orphan_lab() — valid IDs overlap with truncated prefix (reverse direction)
- _vlans_dir() — creates nested parents
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.providers.base import (
    Provider,
    DeployResult,
    DestroyResult,
    NodeActionResult,
    StatusResult,
    VlanPersistenceMixin,
)


# ---------------------------------------------------------------------------
# Concrete classes for testing
# ---------------------------------------------------------------------------


class _VlanProvider(VlanPersistenceMixin):
    """Concrete class using VlanPersistenceMixin."""

    @property
    def name(self) -> str:
        return "test-vlan"

    def __init__(self):
        self.__init_vlan_state__()


class _ConcreteProvider(Provider):
    """Minimal concrete provider for base class method tests."""

    @property
    def name(self) -> str:
        return "test-vlan"

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


# ---------------------------------------------------------------------------
# _vlans_dir
# ---------------------------------------------------------------------------


class TestVlansDir:
    def test_creates_directory_on_first_call(self, tmp_path):
        p = _VlanProvider()
        vlans = p._vlans_dir(tmp_path)
        assert vlans == tmp_path / "vlans"
        assert vlans.is_dir()

    def test_idempotent(self, tmp_path):
        p = _VlanProvider()
        p._vlans_dir(tmp_path)
        p._vlans_dir(tmp_path)  # second call should not fail
        assert (tmp_path / "vlans").is_dir()


# ---------------------------------------------------------------------------
# _save_vlan_allocations — edge cases beyond ssh_coverage
# ---------------------------------------------------------------------------


class TestSaveVlanAllocationsEdge:
    def test_overwrite_existing_file(self, tmp_path):
        """Saving again should overwrite previous data."""
        p = _VlanProvider()
        p._vlan_allocations["lab1"] = {"n1": [100]}
        p._next_vlan["lab1"] = 101
        p._save_vlan_allocations("lab1", tmp_path)

        # Update and save again
        p._vlan_allocations["lab1"]["n2"] = [102, 103]
        p._next_vlan["lab1"] = 104
        p._save_vlan_allocations("lab1", tmp_path)

        data = json.loads((tmp_path / "vlans" / "lab1.test-vlan.json").read_text())
        assert data["allocations"]["n2"] == [102, 103]
        assert data["next_vlan"] == 104

    def test_multiple_labs_independent(self, tmp_path):
        """Two labs get separate files."""
        p = _VlanProvider()
        p._vlan_allocations["lab-a"] = {"r1": [200]}
        p._vlan_allocations["lab-b"] = {"r2": [300]}
        p._next_vlan["lab-a"] = 201
        p._next_vlan["lab-b"] = 301

        p._save_vlan_allocations("lab-a", tmp_path)
        p._save_vlan_allocations("lab-b", tmp_path)

        a = json.loads((tmp_path / "vlans" / "lab-a.test-vlan.json").read_text())
        b = json.loads((tmp_path / "vlans" / "lab-b.test-vlan.json").read_text())
        assert a["allocations"]["r1"] == [200]
        assert b["allocations"]["r2"] == [300]


# ---------------------------------------------------------------------------
# _load_vlan_allocations — edge cases beyond ssh_coverage
# ---------------------------------------------------------------------------


class TestLoadVlanAllocationsEdge:
    def test_wrong_type_allocations(self, tmp_path):
        """allocations field is a string instead of dict — still loads (no crash)."""
        p = _VlanProvider()
        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        (vlans_dir / "lab1.test-vlan.json").write_text(json.dumps({
            "allocations": "not-a-dict",
            "next_vlan": 150,
        }))

        result = p._load_vlan_allocations("lab1", tmp_path)
        assert result is True
        # The code does .get() which returns "not-a-dict" — it's stored as-is
        assert p._vlan_allocations["lab1"] == "not-a-dict"

    def test_empty_file(self, tmp_path):
        """Completely empty file fails to parse."""
        p = _VlanProvider()
        vlans_dir = tmp_path / "vlans"
        vlans_dir.mkdir()
        (vlans_dir / "lab1.test-vlan.json").write_text("")

        result = p._load_vlan_allocations("lab1", tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# _remove_vlan_file — edge cases beyond ssh_coverage
# ---------------------------------------------------------------------------


class TestRemoveVlanFileEdge:
    def test_vlans_dir_does_not_exist(self, tmp_path):
        """When vlans dir doesn't exist, the file can't exist either."""
        p = _VlanProvider()
        # _vlans_dir creates the dir, so the file check just returns False
        p._remove_vlan_file("lab1", tmp_path)
        # No exception


# ---------------------------------------------------------------------------
# _cleanup_orphan_vlans — edge cases beyond ssh_coverage
# ---------------------------------------------------------------------------


class TestCleanupOrphanVlansEdge:
    def test_only_allocations_no_next_vlan(self, tmp_path):
        """Lab has allocations but no next_vlan entry."""
        p = _VlanProvider()
        p._vlan_allocations["orphan"] = {"n1": [500]}
        # No entry in _next_vlan

        p._cleanup_orphan_vlans("orphan", tmp_path)
        assert "orphan" not in p._vlan_allocations

    def test_only_next_vlan_no_allocations(self, tmp_path):
        """Lab has next_vlan but no allocations entry."""
        p = _VlanProvider()
        p._next_vlan["orphan"] = 600

        p._cleanup_orphan_vlans("orphan", tmp_path)
        assert "orphan" not in p._next_vlan

    def test_workspace_exists_but_no_vlans_dir(self, tmp_path):
        """Workspace directory exists but vlans/ subdir does not."""
        p = _VlanProvider()
        p._vlan_allocations["orphan"] = {"n1": [100]}

        # tmp_path exists but has no vlans/ — _remove_vlan_file will create it
        p._cleanup_orphan_vlans("orphan", tmp_path)
        assert "orphan" not in p._vlan_allocations


# ---------------------------------------------------------------------------
# _is_orphan_lab — edge cases beyond ssh_coverage
# ---------------------------------------------------------------------------


class TestIsOrphanLabEdge:
    def test_valid_id_is_prefix_of_lab_id(self):
        """When the valid ID is short and is a prefix of the lab ID under test."""
        # valid_id[:20] = "abcdef" (short), lab_id.startswith("abcdef") = True
        short_valid = "abcdef"
        lab_id = "abcdef12345"  # < 20 chars
        assert VlanPersistenceMixin._is_orphan_lab(lab_id, {short_valid}) is False

    def test_exactly_20_chars_is_long(self):
        """ID of exactly 20 chars uses exact-match path (>= 20)."""
        id_20 = "a" * 20
        assert VlanPersistenceMixin._is_orphan_lab(id_20, {"b" * 20}) is True
        assert VlanPersistenceMixin._is_orphan_lab(id_20, {id_20}) is False


# ---------------------------------------------------------------------------
# cleanup_orphan_resources (Provider base default)
# ---------------------------------------------------------------------------


class TestCleanupOrphanResourcesBase:
    @pytest.mark.asyncio
    async def test_returns_empty_dict(self):
        p = _ConcreteProvider()
        result = await p.cleanup_orphan_resources({"lab1", "lab2"}, Path("/tmp"))
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_none_workspace(self):
        p = _ConcreteProvider()
        result = await p.cleanup_orphan_resources(set())
        assert result == {}


# ---------------------------------------------------------------------------
# VLAN constants
# ---------------------------------------------------------------------------


class TestVlanConstants:
    def test_range_start_lt_end(self):
        assert VlanPersistenceMixin.VLAN_RANGE_START < VlanPersistenceMixin.VLAN_RANGE_END

    def test_default_values(self):
        assert VlanPersistenceMixin.VLAN_RANGE_START == 100
        assert VlanPersistenceMixin.VLAN_RANGE_END == 2049
