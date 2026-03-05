"""Tests for image store path helpers."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.image_store.paths import (
    image_store_root, ensure_image_store, qcow2_path, iol_path,
    manifest_path, rules_path, custom_devices_path, hidden_devices_path, device_overrides_path,
)


# ---------------------------------------------------------------------------
# image_store_root
# ---------------------------------------------------------------------------

class TestImageStoreRoot:
    def test_uses_qcow2_store_when_set(self, tmp_path, monkeypatch):
        store = tmp_path / "custom_store"
        object.__setattr__(settings, "qcow2_store", str(store))
        try:
            result = image_store_root()
            assert result == store
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_falls_back_to_workspace_images(self, tmp_path, monkeypatch):
        object.__setattr__(settings, "qcow2_store", "")
        original_workspace = settings.workspace
        object.__setattr__(settings, "workspace", str(tmp_path / "ws"))
        try:
            result = image_store_root()
            assert result == tmp_path / "ws" / "images"
        finally:
            object.__setattr__(settings, "workspace", original_workspace)

    def test_returns_path_object_with_qcow2_store(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert isinstance(image_store_root(), Path)
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_returns_path_object_without_qcow2_store(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", "")
        original_workspace = settings.workspace
        object.__setattr__(settings, "workspace", str(tmp_path))
        try:
            assert isinstance(image_store_root(), Path)
        finally:
            object.__setattr__(settings, "workspace", original_workspace)

    def test_qcow2_store_none_treated_as_falsy(self, tmp_path):
        original_workspace = settings.workspace
        object.__setattr__(settings, "qcow2_store", "")
        object.__setattr__(settings, "workspace", str(tmp_path))
        try:
            result = image_store_root()
            assert result == tmp_path / "images"
        finally:
            object.__setattr__(settings, "workspace", original_workspace)


# ---------------------------------------------------------------------------
# ensure_image_store
# ---------------------------------------------------------------------------

class TestEnsureImageStore:
    def test_creates_directory(self, tmp_path):
        store = tmp_path / "new_store"
        object.__setattr__(settings, "qcow2_store", str(store))
        try:
            result = ensure_image_store()
            assert result == store
            assert store.is_dir()
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_creates_nested_directory(self, tmp_path):
        store = tmp_path / "a" / "b" / "c"
        object.__setattr__(settings, "qcow2_store", str(store))
        try:
            result = ensure_image_store()
            assert store.is_dir()
            assert result == store
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_idempotent(self, tmp_path):
        store = tmp_path / "idempotent"
        object.__setattr__(settings, "qcow2_store", str(store))
        try:
            ensure_image_store()
            ensure_image_store()
            assert store.is_dir()
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_returns_path_object(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert isinstance(ensure_image_store(), Path)
        finally:
            object.__setattr__(settings, "qcow2_store", "")


# ---------------------------------------------------------------------------
# qcow2_path / iol_path
# ---------------------------------------------------------------------------

class TestQcow2Path:
    def test_appends_filename(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert qcow2_path("disk.qcow2") == tmp_path / "disk.qcow2"
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_returns_path_object(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert isinstance(qcow2_path("x.qcow2"), Path)
        finally:
            object.__setattr__(settings, "qcow2_store", "")


class TestIolPath:
    def test_appends_filename(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert iol_path("image.bin") == tmp_path / "image.bin"
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_returns_path_object(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            assert isinstance(iol_path("image.bin"), Path)
        finally:
            object.__setattr__(settings, "qcow2_store", "")


# ---------------------------------------------------------------------------
# Named JSON paths
# ---------------------------------------------------------------------------

class TestNamedPaths:
    @pytest.fixture(autouse=True)
    def _set_store(self, tmp_path):
        original = settings.qcow2_store
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        yield
        object.__setattr__(settings, "qcow2_store", original)

    def test_manifest_path_filename(self):
        assert manifest_path().name == "manifest.json"

    def test_rules_path_filename(self):
        assert rules_path().name == "rules.json"

    def test_custom_devices_path_filename(self):
        assert custom_devices_path().name == "custom_devices.json"

    def test_hidden_devices_path_filename(self):
        assert hidden_devices_path().name == "hidden_devices.json"

    def test_device_overrides_path_filename(self):
        assert device_overrides_path().name == "device_overrides.json"

    def test_all_return_path_objects(self):
        for fn in (manifest_path, rules_path, custom_devices_path,
                   hidden_devices_path, device_overrides_path):
            assert isinstance(fn(), Path), f"{fn.__name__} did not return Path"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_handles_spaces_in_path(self, tmp_path):
        store = tmp_path / "path with spaces"
        object.__setattr__(settings, "qcow2_store", str(store))
        try:
            result = ensure_image_store()
            assert result == store
            assert store.is_dir()
            assert qcow2_path("file.qcow2") == store / "file.qcow2"
        finally:
            object.__setattr__(settings, "qcow2_store", "")

    def test_named_paths_share_parent(self, tmp_path):
        object.__setattr__(settings, "qcow2_store", str(tmp_path))
        try:
            parents = {fn().parent for fn in (
                manifest_path, rules_path, custom_devices_path,
                hidden_devices_path, device_overrides_path,
            )}
            assert len(parents) == 1
            assert parents.pop() == tmp_path
        finally:
            object.__setattr__(settings, "qcow2_store", "")
