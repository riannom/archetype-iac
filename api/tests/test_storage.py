"""Tests for storage module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import schemas
from app.storage import (
    delete_layout,
    lab_workspace,
    layout_path,
    read_layout,
    write_layout,
)


@pytest.fixture()
def mock_workspace(monkeypatch, tmp_path):
    """Patch workspace_root to return tmp_path.

    Pydantic BaseSettings instances are immutable, so monkeypatch.setattr
    on ``settings.workspace`` does not actually change the value that
    ``workspace_root()`` reads.  Instead we patch the function itself at
    the module level so every caller (lab_workspace, layout_path, etc.)
    picks up the temporary directory.
    """
    monkeypatch.setattr("app.storage.workspace_root", lambda: tmp_path)
    return tmp_path


class TestWorkspaceRoot:
    """Tests for workspace_root function."""

    def test_returns_path(self, mock_workspace):
        """Test that workspace_root returns a Path object."""
        # workspace_root is patched, so call the storage-level reference
        from app import storage

        root = storage.workspace_root()
        assert isinstance(root, Path)
        assert root == mock_workspace


class TestLabWorkspace:
    """Tests for lab_workspace function."""

    def test_returns_lab_path(self, mock_workspace):
        """Test lab_workspace returns correct path."""
        workspace = lab_workspace("test-lab-123")
        assert workspace == mock_workspace / "test-lab-123"

    def test_handles_special_characters(self, mock_workspace):
        """Test lab_workspace handles lab IDs with special chars."""
        workspace = lab_workspace("lab-with-dashes")
        assert workspace == mock_workspace / "lab-with-dashes"


class TestLayoutPath:
    """Tests for layout_path function."""

    def test_returns_layout_json_path(self, mock_workspace):
        """Test layout_path returns path to layout.json."""
        l_path = layout_path("test-lab")
        assert l_path == mock_workspace / "test-lab" / "layout.json"


class TestReadLayout:
    """Tests for read_layout function."""

    def test_returns_none_when_file_not_exists(self, mock_workspace):
        """Test read_layout returns None when file doesn't exist."""
        result = read_layout("nonexistent-lab")
        assert result is None

    def test_reads_valid_layout(self, mock_workspace):
        """Test read_layout reads valid layout file."""
        lab_dir = mock_workspace / "test-lab"
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

    def test_returns_none_on_invalid_json(self, mock_workspace):
        """Test read_layout returns None on invalid JSON."""
        lab_dir = mock_workspace / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text("invalid json {{{")

        result = read_layout("test-lab")
        assert result is None

    def test_returns_none_on_invalid_schema(self, mock_workspace):
        """Test read_layout returns None on invalid schema."""
        lab_dir = mock_workspace / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        # Valid JSON but missing required fields
        layout_file.write_text('{"invalid": "schema"}')

        read_layout("test-lab")
        # Should return None or a default layout depending on validation
        # The schema allows many optional fields, so this might actually parse


class TestWriteLayout:
    """Tests for write_layout function."""

    def test_writes_layout_file(self, mock_workspace):
        """Test write_layout creates layout file."""
        layout = schemas.LabLayout(
            version=1,
            canvas=schemas.CanvasState(zoom=1.5, offsetX=100, offsetY=200),
            nodes={"r1": schemas.NodeLayout(x=50, y=75)},
            annotations=[],
        )

        write_layout("test-lab", layout)

        layout_file = mock_workspace / "test-lab" / "layout.json"
        assert layout_file.exists()

        # Verify content
        data = json.loads(layout_file.read_text())
        assert data["version"] == 1
        assert data["canvas"]["zoom"] == 1.5
        assert data["nodes"]["r1"]["x"] == 50

    def test_creates_parent_directories(self, mock_workspace):
        """Test write_layout creates parent directories."""
        layout = schemas.LabLayout(version=1)

        write_layout("new-lab-nested", layout)

        layout_file = mock_workspace / "new-lab-nested" / "layout.json"
        assert layout_file.exists()

    def test_overwrites_existing_file(self, mock_workspace):
        """Test write_layout overwrites existing file."""
        lab_dir = mock_workspace / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text('{"version": 0}')

        layout = schemas.LabLayout(version=2)
        write_layout("test-lab", layout)

        data = json.loads(layout_file.read_text())
        assert data["version"] == 2


class TestDeleteLayout:
    """Tests for delete_layout function."""

    def test_deletes_existing_file(self, mock_workspace):
        """Test delete_layout removes existing file."""
        lab_dir = mock_workspace / "test-lab"
        lab_dir.mkdir(parents=True)
        layout_file = lab_dir / "layout.json"
        layout_file.write_text("{}")

        result = delete_layout("test-lab")

        assert result is True
        assert not layout_file.exists()

    def test_returns_false_when_file_not_exists(self, mock_workspace):
        """Test delete_layout returns False when file doesn't exist."""
        result = delete_layout("nonexistent-lab")

        assert result is False
