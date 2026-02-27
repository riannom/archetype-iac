"""Tests for ConfigService service-level operations.

Covers list_configs_with_orphan_status, set_active_config, clear_active_config,
delete_configs (bulk with guard), and build_download_zip.

Does NOT duplicate tests from test_config_service_units.py (resolve priority,
save_extracted_config dedup, basic guard).
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import models
from app.config import settings
from app.services.config_service import (
    ActiveConfigGuardError,
    ConfigService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def lab_with_nodes(test_db):
    """Create a lab with two nodes of different device types."""
    lab = models.Lab(
        id="cfg-lab-1",
        name="Config Test Lab",
        owner_id=None,
        provider="docker",
        state="stopped",
        workspace_path="",
    )
    node_a = models.Node(
        id="node-a",
        lab_id=lab.id,
        gui_id="gui-a",
        display_name="R1",
        container_name="r1",
        device="ceos",
    )
    node_b = models.Node(
        id="node-b",
        lab_id=lab.id,
        gui_id="gui-b",
        display_name="SW1",
        container_name="sw1",
        device="srl",
    )
    test_db.add_all([lab, node_a, node_b])
    test_db.commit()
    for obj in [lab, node_a, node_b]:
        test_db.refresh(obj)
    return lab, node_a, node_b


@pytest.fixture()
def snapshots(test_db, lab_with_nodes):
    """Create config snapshots for both nodes."""
    lab, node_a, node_b = lab_with_nodes
    now = datetime.now(timezone.utc)

    snap_a1 = models.ConfigSnapshot(
        id="snap-a1",
        lab_id=lab.id,
        node_name=node_a.container_name,
        content="hostname R1\ninterface Ethernet1",
        content_hash="aaa111",
        snapshot_type="manual",
        device_kind="ceos",
        created_at=now - timedelta(hours=2),
    )
    snap_a2 = models.ConfigSnapshot(
        id="snap-a2",
        lab_id=lab.id,
        node_name=node_a.container_name,
        content="hostname R1\ninterface Ethernet1\ninterface Ethernet2",
        content_hash="aaa222",
        snapshot_type="auto_stop",
        device_kind="ceos",
        created_at=now - timedelta(hours=1),
    )
    snap_b1 = models.ConfigSnapshot(
        id="snap-b1",
        lab_id=lab.id,
        node_name=node_b.container_name,
        content="set / interface ethernet-1/1",
        content_hash="bbb111",
        snapshot_type="manual",
        device_kind="srl",
        created_at=now,
    )
    test_db.add_all([snap_a1, snap_a2, snap_b1])
    test_db.commit()
    for s in [snap_a1, snap_a2, snap_b1]:
        test_db.refresh(s)
    return snap_a1, snap_a2, snap_b1


@pytest.fixture()
def orphan_snapshot(test_db, lab_with_nodes):
    """Create a snapshot whose node_name does not match any existing node."""
    lab, _, _ = lab_with_nodes
    snap = models.ConfigSnapshot(
        id="snap-orphan",
        lab_id=lab.id,
        node_name="deleted-router",
        content="orphan config",
        content_hash="orph1",
        snapshot_type="auto_stop",
        device_kind="ceos",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(snap)
    test_db.commit()
    test_db.refresh(snap)
    return snap


# ---------------------------------------------------------------------------
# TestListConfigsWithOrphanStatus
# ---------------------------------------------------------------------------


class TestListConfigsWithOrphanStatus:
    """Tests for list_configs_with_orphan_status query operation."""

    def test_returns_all_snapshots(self, test_db, lab_with_nodes, snapshots):
        """Should return all snapshots for the lab."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id)
        assert len(results) == 3

    def test_filter_by_node_name(self, test_db, lab_with_nodes, snapshots):
        """Should filter by node_name when provided."""
        lab, node_a, _ = lab_with_nodes
        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id, node_name=node_a.container_name)
        assert len(results) == 2
        assert all(r["node_name"] == "r1" for r in results)

    def test_filter_by_device_kind(self, test_db, lab_with_nodes, snapshots):
        """Should filter by device_kind when provided."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id, device_kind="srl")
        assert len(results) == 1
        assert results[0]["device_kind"] == "srl"

    def test_orphaned_only(self, test_db, lab_with_nodes, snapshots, orphan_snapshot):
        """Should only return orphaned snapshots when orphaned_only=True."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id, orphaned_only=True)
        assert len(results) == 1
        assert results[0]["node_name"] == "deleted-router"
        assert results[0]["is_orphaned"] is True

    def test_is_active_flag(self, test_db, lab_with_nodes, snapshots):
        """Should mark snapshot as active when node references it."""
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots
        node_a.active_config_snapshot_id = snap_a1.id
        test_db.commit()

        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id)
        active_results = [r for r in results if r["is_active"]]
        assert len(active_results) == 1
        assert active_results[0]["id"] == "snap-a1"

    def test_empty_lab(self, test_db):
        """Should return empty list for a lab with no snapshots."""
        lab = models.Lab(
            id="empty-lab",
            name="Empty Lab",
            owner_id=None,
            provider="docker",
            state="stopped",
            workspace_path="",
        )
        test_db.add(lab)
        test_db.commit()

        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status("empty-lab")
        assert results == []

    def test_is_orphaned_flag_for_existing_nodes(self, test_db, lab_with_nodes, snapshots):
        """Snapshots belonging to current nodes should not be marked orphaned."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        results = svc.list_configs_with_orphan_status(lab.id)
        assert all(r["is_orphaned"] is False for r in results)


# ---------------------------------------------------------------------------
# TestSetActiveConfig
# ---------------------------------------------------------------------------


class TestSetActiveConfig:
    """Tests for set_active_config mutation."""

    def test_sets_fk_and_syncs_content(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should set active_config_snapshot_id and sync config_json."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        svc = ConfigService(test_db)
        result = svc.set_active_config(node_a.id, snap_a1.id)
        test_db.commit()

        test_db.refresh(node_a)
        assert node_a.active_config_snapshot_id == snap_a1.id
        config = json.loads(node_a.config_json)
        assert config["startup-config"] == snap_a1.content

    def test_nonexistent_node_raises(self, test_db, lab_with_nodes, snapshots):
        """Should raise ValueError for a nonexistent node_id."""
        _, _, _ = lab_with_nodes
        snap_a1, _, _ = snapshots
        svc = ConfigService(test_db)
        with pytest.raises(ValueError, match="Node not found"):
            svc.set_active_config("nonexistent-node", snap_a1.id)

    def test_nonexistent_snapshot_raises(self, test_db, lab_with_nodes, snapshots):
        """Should raise ValueError for a nonexistent snapshot_id."""
        _, node_a, _ = lab_with_nodes
        svc = ConfigService(test_db)
        with pytest.raises(ValueError, match="Snapshot not found"):
            svc.set_active_config(node_a.id, "nonexistent-snapshot")

    def test_cross_lab_snapshot_raises(self, test_db, lab_with_nodes, snapshots):
        """Should raise ValueError when snapshot belongs to a different lab."""
        _, node_a, _ = lab_with_nodes

        other_lab = models.Lab(
            id="other-lab",
            name="Other",
            owner_id=None,
            provider="docker",
            state="stopped",
            workspace_path="",
        )
        test_db.add(other_lab)
        test_db.commit()

        cross_snap = models.ConfigSnapshot(
            id="cross-snap",
            lab_id="other-lab",
            node_name="r1",
            content="cross",
            content_hash="cross1",
            snapshot_type="manual",
            device_kind="ceos",
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(cross_snap)
        test_db.commit()

        svc = ConfigService(test_db)
        with pytest.raises(ValueError, match="same lab"):
            svc.set_active_config(node_a.id, cross_snap.id)

    def test_writes_workspace_file(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should write the startup-config file to the workspace."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        svc = ConfigService(test_db)
        svc.set_active_config(node_a.id, snap_a1.id)

        config_file = tmp_path / lab.id / "configs" / node_a.container_name / "startup-config"
        assert config_file.exists()
        assert config_file.read_text() == snap_a1.content


# ---------------------------------------------------------------------------
# TestClearActiveConfig
# ---------------------------------------------------------------------------


class TestClearActiveConfig:
    """Tests for clear_active_config mutation."""

    def test_clears_fk_and_config_json(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should clear active_config_snapshot_id and remove startup-config from config_json."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        # First set an active config
        svc = ConfigService(test_db)
        svc.set_active_config(node_a.id, snap_a1.id)
        test_db.commit()
        test_db.refresh(node_a)
        assert node_a.active_config_snapshot_id == snap_a1.id

        # Now clear it
        svc.clear_active_config(node_a.id)
        test_db.commit()

        test_db.refresh(node_a)
        assert node_a.active_config_snapshot_id is None
        # config_json should not contain startup-config
        if node_a.config_json:
            config = json.loads(node_a.config_json)
            assert "startup-config" not in config

    def test_deletes_workspace_file(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should delete the startup-config file from workspace."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        svc = ConfigService(test_db)
        svc.set_active_config(node_a.id, snap_a1.id)
        test_db.commit()

        config_file = tmp_path / lab.id / "configs" / node_a.container_name / "startup-config"
        assert config_file.exists()

        svc.clear_active_config(node_a.id)
        assert not config_file.exists()

    def test_nonexistent_node_raises(self, test_db):
        """Should raise ValueError for a nonexistent node_id."""
        svc = ConfigService(test_db)
        with pytest.raises(ValueError, match="Node not found"):
            svc.clear_active_config("nonexistent-node")


# ---------------------------------------------------------------------------
# TestBulkDeleteSnapshots
# ---------------------------------------------------------------------------


class TestBulkDeleteSnapshots:
    """Tests for delete_configs with active config guard."""

    def test_deletes_multiple_by_ids(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should delete multiple snapshots by ID list."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, _, _ = lab_with_nodes
        snap_a1, snap_a2, _ = snapshots

        svc = ConfigService(test_db)
        result = svc.delete_configs(lab.id, snapshot_ids=["snap-a1", "snap-a2"])
        assert result["deleted_count"] == 2

    def test_guard_blocks_active_without_force(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should raise ActiveConfigGuardError when deleting an active snapshot."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        node_a.active_config_snapshot_id = snap_a1.id
        test_db.commit()

        svc = ConfigService(test_db)
        with pytest.raises(ActiveConfigGuardError) as exc_info:
            svc.delete_configs(lab.id, snapshot_ids=["snap-a1"])

        assert len(exc_info.value.active_snapshots) == 1
        assert exc_info.value.active_snapshots[0]["snapshot_id"] == "snap-a1"

    def test_force_overrides_guard(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """force=True should override the active config guard."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_a, _ = lab_with_nodes
        snap_a1, _, _ = snapshots

        node_a.active_config_snapshot_id = snap_a1.id
        test_db.commit()

        svc = ConfigService(test_db)
        result = svc.delete_configs(lab.id, snapshot_ids=["snap-a1"], force=True)
        assert result["deleted_count"] == 1

        # Node's FK should be cleared
        test_db.refresh(node_a)
        assert node_a.active_config_snapshot_id is None

    def test_removes_workspace_files_when_no_remaining(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should remove workspace config dir when node has no remaining snapshots."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, node_b, _ = lab_with_nodes
        _, _, snap_b1 = snapshots

        # Create a workspace file for sw1
        config_dir = tmp_path / lab.id / "configs" / "sw1"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "startup-config").write_text("test")

        svc = ConfigService(test_db)
        result = svc.delete_configs(lab.id, snapshot_ids=["snap-b1"])
        assert result["deleted_count"] == 1
        assert "sw1" in result["cleaned_workspace_dirs"]
        assert not config_dir.exists()

    def test_no_matching_snapshots(self, test_db, lab_with_nodes):
        """Should return 0 when no matching snapshots found."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        result = svc.delete_configs(lab.id, snapshot_ids=["nonexistent-id"])
        assert result["deleted_count"] == 0

    def test_delete_by_node_name(self, test_db, lab_with_nodes, snapshots, tmp_path, monkeypatch):
        """Should delete all snapshots for a specific node_name."""
        monkeypatch.setattr(settings, "workspace", str(tmp_path))
        lab, _, _ = lab_with_nodes

        svc = ConfigService(test_db)
        result = svc.delete_configs(lab.id, node_name="r1")
        assert result["deleted_count"] == 2


# ---------------------------------------------------------------------------
# TestZipDownload
# ---------------------------------------------------------------------------


class TestZipDownload:
    """Tests for build_download_zip."""

    def test_creates_zip_with_configs(self, test_db, lab_with_nodes, snapshots):
        """Should create a zip containing config files and metadata."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        buf = svc.build_download_zip(lab.id, all_configs=True)

        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            # Should contain config files + metadata
            assert any("r1/" in n and "startup-config" in n for n in names)
            assert any("sw1/" in n and "startup-config" in n for n in names)
            assert "r1/metadata.json" in names
            assert "sw1/metadata.json" in names

    def test_includes_metadata(self, test_db, lab_with_nodes, snapshots):
        """Metadata JSON should contain snapshot details."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        buf = svc.build_download_zip(lab.id, all_configs=True)

        with zipfile.ZipFile(buf, "r") as zf:
            meta_raw = zf.read("r1/metadata.json")
            meta = json.loads(meta_raw)
            assert len(meta) == 2  # snap_a1 and snap_a2
            ids = {m["id"] for m in meta}
            assert "snap-a1" in ids
            assert "snap-a2" in ids
            # Each metadata entry should have required fields
            for entry in meta:
                assert "timestamp" in entry
                assert "type" in entry
                assert "content_hash" in entry
                assert "device_kind" in entry

    def test_filter_by_node_names(self, test_db, lab_with_nodes, snapshots):
        """Should only include snapshots for requested node_names."""
        lab, _, _ = lab_with_nodes
        svc = ConfigService(test_db)
        buf = svc.build_download_zip(lab.id, node_names=["sw1"])

        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            assert any("sw1/" in n for n in names)
            assert not any("r1/" in n for n in names)

    def test_no_snapshots_raises(self, test_db):
        """Should raise ValueError when no matching snapshots exist."""
        lab = models.Lab(
            id="empty-zip-lab",
            name="Empty",
            owner_id=None,
            provider="docker",
            state="stopped",
            workspace_path="",
        )
        test_db.add(lab)
        test_db.commit()

        svc = ConfigService(test_db)
        with pytest.raises(ValueError, match="No config snapshots found"):
            svc.build_download_zip("empty-zip-lab", all_configs=True)
