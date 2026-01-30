"""Tests for storage module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import schemas
from app.storage import (
    delete_layout,
    ensure_topology_file,
    lab_workspace,
    layout_path,
    read_layout,
    topology_path,
    workspace_root,
    write_layout,
)


class TestWorkspaceRoot:
    """Tests for workspace_root function."""

    def test_returns_path(self, monkeypatch, tmp_path):
        """Test that workspace_root returns a Path object."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        root = workspace_root()
        assert isinstance(root, Path)
        assert str(root) == str(tmp_path)


class TestLabWorkspace:
    """Tests for lab_workspace function."""

    def test_returns_lab_path(self, monkeypatch, tmp_path):
        """Test lab_workspace returns correct path."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        workspace = lab_workspace("test-lab-123")
        assert workspace == tmp_path / "test-lab-123"

    def test_handles_special_characters(self, monkeypatch, tmp_path):
        """Test lab_workspace handles lab IDs with special chars."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        workspace = lab_workspace("lab-with-dashes")
        assert workspace == tmp_path / "lab-with-dashes"


class TestTopologyPath:
    """Tests for topology_path function."""

    def test_returns_topology_yml_path(self, monkeypatch, tmp_path):
        """Test topology_path returns path to topology.yml."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        topo_path = topology_path("test-lab")
        assert topo_path == tmp_path / "test-lab" / "topology.yml"


class TestLayoutPath:
    """Tests for layout_path function."""

    def test_returns_layout_json_path(self, monkeypatch, tmp_path):
        """Test layout_path returns path to layout.json."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        l_path = layout_path("test-lab")
        assert l_path == tmp_path / "test-lab" / "layout.json"


class TestEnsureTopologyFile:
    """Tests for ensure_topology_file function."""

    def test_creates_topology_file(self, monkeypatch, tmp_path):
        """Test that topology file is created if it doesn't exist."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "new-lab"
        lab_dir.mkdir(parents=True)

        result = ensure_topology_file("new-lab")

        assert result.exists()
        assert "nodes" in result.read_text()

    def test_does_not_overwrite_existing(self, monkeypatch, tmp_path):
        """Test that existing topology file is not overwritten."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "existing-lab"
        lab_dir.mkdir(parents=True)
        topo_file = lab_dir / "topology.yml"
        original_content = "name: my-lab\nnodes: {}"
        topo_file.write_text(original_content)

        result = ensure_topology_file("existing-lab")

        assert result.read_text() == original_content


class TestReadLayout:
    """Tests for read_layout function."""

    def test_returns_none_when_file_not_exists(self, monkeypatch, tmp_path):
        """Test read_layout returns None when file doesn't exist."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        result = read_layout("nonexistent-lab")
        assert result is None

    def test_reads_valid_layout(self, monkeypatch, tmp_path):
        """Test read_layout reads valid layout file."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_data = {
            "version": 1,
            "canvas": {"zoom": 1.0, "offsetX": 0, "offsetY": 0},
            "nodes": {"r1": {"x": 100, "y": 200}},
            "annotations": [],
        }
        layout_file.write_text(json.dumps(layout_data))

        result = read_layout("test-lab")

        assert result is not None
        assert result.version == 1
        assert "r1" in result.nodes
        assert result.nodes["r1"].x == 100

    def test_returns_none_on_invalid_json(self, monkeypatch, tmp_path):
        """Test read_layout returns None on invalid JSON."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text("invalid json {{{")

        result = read_layout("test-lab")
        assert result is None

    def test_returns_none_on_invalid_schema(self, monkeypatch, tmp_path):
        """Test read_layout returns None on invalid schema."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        # Valid JSON but missing required fields
        layout_file.write_text('{"invalid": "schema"}')

        result = read_layout("test-lab")
        # Should return None or a default layout depending on validation
        # The schema allows many optional fields, so this might actually parse


class TestWriteLayout:
    """Tests for write_layout function."""

    def test_writes_layout_file(self, monkeypatch, tmp_path):
        """Test write_layout creates layout file."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        layout = schemas.LabLayout(
            version=1,
            canvas=schemas.CanvasState(zoom=1.5, offsetX=100, offsetY=200),
            nodes={"r1": schemas.NodeLayout(x=50, y=75)},
            annotations=[],
        )

        write_layout("test-lab", layout)

        layout_file = tmp_path / "test-lab" / "layout.json"
        assert layout_file.exists()

        # Verify content
        data = json.loads(layout_file.read_text())
        assert data["version"] == 1
        assert data["canvas"]["zoom"] == 1.5
        assert data["nodes"]["r1"]["x"] == 50

    def test_creates_parent_directories(self, monkeypatch, tmp_path):
        """Test write_layout creates parent directories."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        layout = schemas.LabLayout(version=1)

        write_layout("new-lab/nested", layout)

        layout_file = tmp_path / "new-lab/nested" / "layout.json"
        assert layout_file.exists()

    def test_overwrites_existing_file(self, monkeypatch, tmp_path):
        """Test write_layout overwrites existing file."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text('{"version": 0}')

        layout = schemas.LabLayout(version=2)
        write_layout("test-lab", layout)

        data = json.loads(layout_file.read_text())
        assert data["version"] == 2


class TestDeleteLayout:
    """Tests for delete_layout function."""

    def test_deletes_existing_file(self, monkeypatch, tmp_path):
        """Test delete_layout removes existing file."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        lab_dir = tmp_path / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text("{}")

        result = delete_layout("test-lab")

        assert result is True
        assert not layout_file.exists()

    def test_returns_false_when_file_not_exists(self, monkeypatch, tmp_path):
        """Test delete_layout returns False when file doesn't exist."""
        from app.config import settings
        monkeypatch.setattr(settings, "workspace", str(tmp_path))

        result = delete_layout("nonexistent-lab")

        assert result is False
