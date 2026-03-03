"""Tests for agent/image_metadata.py.

Covers:
- JSON file read/write operations
- Missing file handling
- Corruption recovery (malformed JSON)
- Atomic writes via os.replace
- fcntl.flock concurrent access patterns
- set/lookup operations for Docker images, tags, and file paths
"""
from __future__ import annotations

import json
import os
import threading
from unittest.mock import patch

import pytest

from agent.config import settings


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path, monkeypatch):
    """Reset module-level cached paths and redirect image_store_path.

    The module caches _METADATA_PATH and _LOCK_PATH as globals, so we
    must clear them between tests to pick up the tmp_path redirect.
    """
    monkeypatch.setattr(settings, "image_store_path", str(tmp_path / "images"))
    os.makedirs(str(tmp_path / "images"), exist_ok=True)

    import agent.image_metadata as mod
    mod._METADATA_PATH = None
    mod._LOCK_PATH = None
    yield
    mod._METADATA_PATH = None
    mod._LOCK_PATH = None


# ---------------------------------------------------------------------------
# 1. _read_metadata / _write_metadata basics
# ---------------------------------------------------------------------------


class TestReadWriteMetadata:
    """Tests for _read_metadata and _write_metadata low-level functions."""

    def test_read_missing_file_returns_empty(self):
        """When metadata file does not exist, returns empty structure."""
        from agent.image_metadata import _read_metadata
        data = _read_metadata()
        assert data == {"images": {}, "files": {}}

    def test_write_then_read_roundtrip(self):
        """Data written with _write_metadata can be read back."""
        from agent.image_metadata import _read_metadata, _write_metadata
        payload = {
            "images": {"sha256:abc": {"device_id": "ceos", "tags": ["ceos:latest"], "source": "import"}},
            "files": {},
        }
        _write_metadata(payload)
        result = _read_metadata()
        assert result["images"]["sha256:abc"]["device_id"] == "ceos"

    def test_read_corrupt_json_returns_empty(self, tmp_path):
        """Malformed JSON on disk returns empty structure instead of crashing."""
        from agent.image_metadata import _metadata_path, _read_metadata
        path = _metadata_path()
        path.write_text("{this is not valid json!!!")
        data = _read_metadata()
        assert data == {"images": {}, "files": {}}

    def test_read_non_dict_json_returns_empty(self, tmp_path):
        """JSON that decodes to a non-dict type returns empty structure."""
        from agent.image_metadata import _metadata_path, _read_metadata
        path = _metadata_path()
        path.write_text('"just a string"')
        data = _read_metadata()
        assert data == {"images": {}, "files": {}}

    def test_read_partial_keys_fills_defaults(self, tmp_path):
        """JSON missing 'images' or 'files' keys gets them filled in."""
        from agent.image_metadata import _metadata_path, _read_metadata
        path = _metadata_path()
        path.write_text('{"images": {"sha256:x": {"device_id": "linux"}}}')
        data = _read_metadata()
        assert "sha256:x" in data["images"]
        assert data["files"] == {}

    def test_write_creates_parent_directory(self, tmp_path, monkeypatch):
        """_write_metadata creates parent directories if they don't exist."""
        import agent.image_metadata as mod
        deep_path = tmp_path / "deep" / "nested" / "images"
        monkeypatch.setattr(settings, "image_store_path", str(deep_path))
        mod._METADATA_PATH = None
        mod._LOCK_PATH = None

        from agent.image_metadata import _write_metadata
        _write_metadata({"images": {}, "files": {}})
        assert (deep_path / "image-metadata.json").exists()


# ---------------------------------------------------------------------------
# 2. Atomic write via os.replace
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """Tests verifying atomic writes use temp file + os.replace."""

    def test_write_is_atomic(self):
        """os.replace is called to swap temp file into final path."""
        from agent.image_metadata import _write_metadata
        with patch("agent.image_metadata.os.replace", wraps=os.replace) as mock_replace:
            _write_metadata({"images": {}, "files": {}})
        assert mock_replace.call_count == 1

    def test_failed_write_cleans_temp_file(self):
        """On write error the temp file is removed."""
        from agent.image_metadata import _write_metadata, _metadata_path
        # Force json.dump to fail by passing non-serializable data
        with pytest.raises(TypeError):
            _write_metadata({"images": object()})

        # Verify no .tmp files left behind
        parent = _metadata_path().parent
        tmp_files = list(parent.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# 3. Locked operations (load_metadata / save_metadata)
# ---------------------------------------------------------------------------


class TestLockedOperations:
    """Tests for the _with_lock decorator and locked public API."""

    def test_load_metadata_empty(self):
        """load_metadata returns empty structure when no file exists."""
        from agent.image_metadata import load_metadata
        data = load_metadata()
        assert data == {"images": {}, "files": {}}

    def test_save_then_load(self):
        """save_metadata + load_metadata round-trip works under lock."""
        from agent.image_metadata import load_metadata, save_metadata
        payload = {
            "images": {"sha256:def": {"device_id": "srlinux", "tags": [], "source": "api"}},
            "files": {"/images/disk.qcow2": {"device_id": "iosv", "source": "upload"}},
        }
        save_metadata(payload)
        result = load_metadata()
        assert result["images"]["sha256:def"]["device_id"] == "srlinux"
        assert result["files"]["/images/disk.qcow2"]["device_id"] == "iosv"


# ---------------------------------------------------------------------------
# 4. set_docker_image_metadata / lookup
# ---------------------------------------------------------------------------


class TestDockerImageMetadata:
    """Tests for Docker image metadata set/lookup functions."""

    def test_set_and_lookup_by_image_id(self):
        """set_docker_image_metadata stores data retrievable by image_id."""
        from agent.image_metadata import (
            lookup_device_id_by_image_id,
            set_docker_image_metadata,
        )
        set_docker_image_metadata(
            image_id="sha256:abc123",
            tags=["ceos:4.32.0", "ceos:latest"],
            device_id="arista_ceos",
            source="import",
        )
        result = lookup_device_id_by_image_id("sha256:abc123")
        assert result == "arista_ceos"

    def test_lookup_by_image_id_missing(self):
        """Lookup for nonexistent image_id returns None."""
        from agent.image_metadata import lookup_device_id_by_image_id
        assert lookup_device_id_by_image_id("sha256:nonexistent") is None

    def test_lookup_by_tag(self):
        """lookup_device_id_by_tag finds device by Docker tag."""
        from agent.image_metadata import (
            lookup_device_id_by_tag,
            set_docker_image_metadata,
        )
        set_docker_image_metadata(
            image_id="sha256:tag_test",
            tags=["srlinux:24.3.2", "srlinux:latest"],
            device_id="nokia_srlinux",
            source="pull",
        )
        assert lookup_device_id_by_tag("srlinux:24.3.2") == "nokia_srlinux"
        assert lookup_device_id_by_tag("srlinux:latest") == "nokia_srlinux"
        assert lookup_device_id_by_tag("srlinux:old") is None

    def test_overwrite_image_metadata(self):
        """Setting metadata for the same image_id overwrites previous."""
        from agent.image_metadata import (
            lookup_device_id_by_image_id,
            set_docker_image_metadata,
        )
        set_docker_image_metadata(
            image_id="sha256:overwrite",
            tags=["img:v1"],
            device_id="old_device",
            source="api",
        )
        set_docker_image_metadata(
            image_id="sha256:overwrite",
            tags=["img:v2"],
            device_id="new_device",
            source="api",
        )
        assert lookup_device_id_by_image_id("sha256:overwrite") == "new_device"


# ---------------------------------------------------------------------------
# 5. set_file_image_metadata / lookup
# ---------------------------------------------------------------------------


class TestFileImageMetadata:
    """Tests for file-based image metadata set/lookup functions."""

    def test_set_and_lookup_by_path(self):
        """set_file_image_metadata stores data retrievable by path."""
        from agent.image_metadata import (
            lookup_device_id_by_path,
            set_file_image_metadata,
        )
        set_file_image_metadata(
            path="/var/lib/archetype/images/vios.qcow2",
            device_id="cisco_iosv",
            source="upload",
        )
        result = lookup_device_id_by_path("/var/lib/archetype/images/vios.qcow2")
        assert result == "cisco_iosv"

    def test_lookup_by_path_missing(self):
        """Lookup for nonexistent file path returns None."""
        from agent.image_metadata import lookup_device_id_by_path
        assert lookup_device_id_by_path("/nonexistent/path.qcow2") is None


# ---------------------------------------------------------------------------
# 6. Concurrent access
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    """Tests verifying concurrent writers don't corrupt metadata."""

    def test_concurrent_writes_no_data_loss(self):
        """Multiple threads writing simultaneously should not corrupt JSON."""
        from agent.image_metadata import (
            load_metadata,
            set_docker_image_metadata,
            set_file_image_metadata,
        )

        errors: list[Exception] = []

        def write_docker(i: int):
            try:
                set_docker_image_metadata(
                    image_id=f"sha256:thread-{i}",
                    tags=[f"img-{i}:latest"],
                    device_id=f"device-{i}",
                    source="test",
                )
            except Exception as e:
                errors.append(e)

        def write_file(i: int):
            try:
                set_file_image_metadata(
                    path=f"/images/disk-{i}.qcow2",
                    device_id=f"file-device-{i}",
                    source="test",
                )
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=write_docker, args=(i,)))
            threads.append(threading.Thread(target=write_file, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent writes produced errors: {errors}"

        # Verify all entries are present (lock serialized them)
        data = load_metadata()
        for i in range(5):
            assert f"sha256:thread-{i}" in data["images"]
            assert f"/images/disk-{i}.qcow2" in data["files"]
