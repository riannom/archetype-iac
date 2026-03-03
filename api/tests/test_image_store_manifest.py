"""Unit tests for app.image_store.manifest module.

Directly tests:
- load_manifest() — loading from disk, missing file, corrupt JSON
- save_manifest() — writing to disk, pretty-print formatting
- find_image_by_id() / find_image_by_reference() — manifest queries
- find_image_reference() — device image lookup with version/default/fallback
- _normalize_manifest_images() — canonicalization and backfill logic
- Round-trip: save then load produces equivalent data
"""
from __future__ import annotations

import json

import pytest

from app.image_store.manifest import (
    _backfill_single_image_defaults,
    _normalize_manifest_images,
    find_image_by_id,
    find_image_by_reference,
    find_image_reference,
    load_manifest,
    save_manifest,
)


@pytest.fixture(autouse=True)
def _isolate_image_store(tmp_path, monkeypatch):
    """Redirect image store paths to tmp_path and bypass catalog DB mode."""
    monkeypatch.setattr("app.image_store.paths.ensure_image_store", lambda: tmp_path)
    monkeypatch.setattr("app.image_store.paths.image_store_root", lambda: tmp_path)
    monkeypatch.setattr(
        "app.image_store.manifest.manifest_path", lambda: tmp_path / "manifest.json"
    )
    monkeypatch.setattr(
        "app.services.catalog_service.catalog_is_seeded",
        lambda _session: False,
    )


class TestLoadManifest:
    """Tests for load_manifest()."""

    def test_load_missing_file_returns_empty(self, tmp_path):
        """Missing manifest.json returns {'images': []}."""
        manifest = load_manifest()
        assert manifest == {"images": []}

    def test_load_valid_manifest(self, tmp_path):
        """A valid manifest.json is loaded and images are accessible."""
        data = {
            "images": [
                {
                    "id": "docker:linux:latest",
                    "kind": "docker",
                    "reference": "linux:latest",
                    "filename": "linux.tar",
                    "device_id": "linux",
                    "compatible_devices": ["linux"],
                }
            ]
        }
        (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

        manifest = load_manifest()
        assert len(manifest["images"]) == 1
        assert manifest["images"][0]["id"] == "docker:linux:latest"

    def test_load_corrupt_json_raises(self, tmp_path):
        """Corrupt JSON in manifest.json raises an error."""
        (tmp_path / "manifest.json").write_text("{{not json}}", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            load_manifest()

    def test_load_applies_normalization(self, tmp_path):
        """load_manifest() calls _normalize_manifest_images on the result."""
        data = {
            "images": [
                {
                    "id": "qcow2:vios-15.9",
                    "kind": "qcow2",
                    "reference": "/images/vios-15.9.qcow2",
                    "filename": "vios-15.9.qcow2",
                    "device_id": "iosv",
                    "compatible_devices": ["iosv"],
                }
            ]
        }
        (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

        manifest = load_manifest()
        # iosv should be canonicalized to cisco_iosv
        assert manifest["images"][0]["device_id"] == "cisco_iosv"

    def test_load_empty_images_list(self, tmp_path):
        """An empty images array loads without error."""
        (tmp_path / "manifest.json").write_text(
            json.dumps({"images": []}), encoding="utf-8"
        )
        manifest = load_manifest()
        assert manifest["images"] == []


class TestSaveManifest:
    """Tests for save_manifest()."""

    def test_save_creates_file(self, tmp_path):
        """save_manifest() writes manifest.json to disk."""
        manifest_file = tmp_path / "manifest.json"
        assert not manifest_file.exists()

        save_manifest({"images": [{"id": "test:1", "kind": "docker"}]})
        assert manifest_file.exists()

    def test_save_writes_pretty_json(self, tmp_path):
        """Saved file uses indent=2 pretty-print."""
        save_manifest({"images": [{"id": "test:1"}]})

        content = (tmp_path / "manifest.json").read_text(encoding="utf-8")
        assert "\n" in content
        # Verify it round-trips as valid JSON
        parsed = json.loads(content)
        assert parsed["images"][0]["id"] == "test:1"

    def test_save_normalizes_before_writing(self, tmp_path):
        """save_manifest() normalizes device IDs before writing."""
        save_manifest(
            {
                "images": [
                    {
                        "id": "qcow2:vios",
                        "kind": "qcow2",
                        "reference": "/images/vios.qcow2",
                        "filename": "vios.qcow2",
                        "device_id": "iosv",
                        "compatible_devices": ["iosv"],
                    }
                ]
            }
        )

        content = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        assert content["images"][0]["device_id"] == "cisco_iosv"

    def test_save_overwrites_existing(self, tmp_path):
        """Calling save_manifest() twice overwrites the previous file."""
        save_manifest({"images": [{"id": "old"}]})
        save_manifest({"images": [{"id": "new"}]})

        content = json.loads(
            (tmp_path / "manifest.json").read_text(encoding="utf-8")
        )
        assert len(content["images"]) == 1
        assert content["images"][0]["id"] == "new"


class TestRoundTrip:
    """Round-trip: save then load produces equivalent data."""

    def test_round_trip_preserves_images(self, tmp_path):
        """Data survives a save-then-load cycle."""
        original = {
            "images": [
                {
                    "id": "docker:linux:latest",
                    "kind": "docker",
                    "reference": "linux:latest",
                    "filename": "linux.tar",
                    "device_id": "linux",
                    "compatible_devices": ["linux"],
                    "is_default": False,
                    "default_for_devices": [],
                }
            ]
        }
        save_manifest(original)
        loaded = load_manifest()

        assert len(loaded["images"]) == 1
        img = loaded["images"][0]
        assert img["id"] == "docker:linux:latest"
        assert img["kind"] == "docker"
        assert img["reference"] == "linux:latest"
        assert img["device_id"] == "linux"

    def test_round_trip_single_image_gets_default(self, tmp_path):
        """A single runnable image is auto-marked as default after round-trip."""
        original = {
            "images": [
                {
                    "id": "docker:ceos:4.28",
                    "kind": "docker",
                    "reference": "ceos:4.28",
                    "filename": "ceos.tar",
                    "device_id": "ceos",
                    "compatible_devices": ["ceos"],
                    "is_default": False,
                    "default_for_devices": [],
                }
            ]
        }
        save_manifest(original)
        loaded = load_manifest()

        assert loaded["images"][0]["is_default"] is True


class TestFindImageByIdAndReference:
    """Tests for find_image_by_id() and find_image_by_reference()."""

    def test_find_by_id_existing(self):
        manifest = {
            "images": [
                {"id": "docker:a:1.0", "reference": "/ref/a"},
                {"id": "docker:b:2.0", "reference": "/ref/b"},
            ]
        }
        result = find_image_by_id(manifest, "docker:b:2.0")
        assert result is not None
        assert result["reference"] == "/ref/b"

    def test_find_by_id_missing(self):
        manifest = {"images": [{"id": "docker:a:1.0"}]}
        assert find_image_by_id(manifest, "nonexistent") is None

    def test_find_by_id_empty_manifest(self):
        assert find_image_by_id({"images": []}, "any") is None

    def test_find_by_reference_existing(self):
        manifest = {
            "images": [
                {"id": "img1", "reference": "ceos:4.28"},
                {"id": "img2", "reference": "linux:latest"},
            ]
        }
        result = find_image_by_reference(manifest, "ceos:4.28")
        assert result is not None
        assert result["id"] == "img1"

    def test_find_by_reference_missing(self):
        manifest = {"images": [{"id": "img1", "reference": "ceos:4.28"}]}
        assert find_image_by_reference(manifest, "missing:ref") is None


class TestNormalizeManifestImages:
    """Tests for _normalize_manifest_images()."""

    def test_non_list_images_is_noop(self):
        """If images key is not a list, normalization is a no-op."""
        manifest = {"images": "not_a_list"}
        _normalize_manifest_images(manifest)
        assert manifest["images"] == "not_a_list"

    def test_non_dict_entries_skipped(self):
        """Non-dict entries in the images list are skipped without error."""
        manifest = {"images": ["string_entry", 42, None]}
        _normalize_manifest_images(manifest)
        # Should not raise; entries remain as-is
        assert manifest["images"] == ["string_entry", 42, None]

    def test_vendor_field_set_for_known_device(self):
        """Normalization sets vendor for recognized device IDs."""
        manifest = {
            "images": [
                {
                    "id": "docker:ceos:4.28",
                    "kind": "docker",
                    "device_id": "ceos",
                    "compatible_devices": ["ceos"],
                }
            ]
        }
        _normalize_manifest_images(manifest)
        assert manifest["images"][0].get("vendor") == "Arista"

    def test_default_for_devices_backfilled_from_is_default(self):
        """Legacy is_default=True gets converted to default_for_devices list."""
        manifest = {
            "images": [
                {
                    "id": "docker:ceos:4.28",
                    "kind": "docker",
                    "device_id": "ceos",
                    "compatible_devices": ["ceos"],
                    "is_default": True,
                }
            ]
        }
        _normalize_manifest_images(manifest)
        img = manifest["images"][0]
        assert "ceos" in img.get("default_for_devices", [])
        assert img["is_default"] is True


class TestBackfillSingleImageDefaults:
    """Tests for _backfill_single_image_defaults()."""

    def test_single_runnable_image_marked_default(self):
        """A single runnable image for a device is auto-marked default."""
        manifest = {
            "images": [
                {
                    "kind": "docker",
                    "device_id": "linux",
                    "compatible_devices": ["linux"],
                    "is_default": False,
                    "default_for_devices": [],
                }
            ]
        }
        _backfill_single_image_defaults(manifest)
        assert manifest["images"][0]["is_default"] is True
        assert "linux" in manifest["images"][0]["default_for_devices"]

    def test_multiple_runnable_images_not_forced_default(self):
        """Multiple runnable images for the same device are not auto-defaulted."""
        manifest = {
            "images": [
                {
                    "kind": "docker",
                    "device_id": "ceos",
                    "compatible_devices": ["ceos"],
                    "is_default": False,
                    "default_for_devices": [],
                },
                {
                    "kind": "docker",
                    "device_id": "ceos",
                    "compatible_devices": ["ceos"],
                    "is_default": False,
                    "default_for_devices": [],
                },
            ]
        }
        _backfill_single_image_defaults(manifest)
        assert manifest["images"][0]["is_default"] is False
        assert manifest["images"][1]["is_default"] is False

    def test_non_runnable_kind_ignored(self):
        """Non-runnable image kinds (e.g. 'iol') are not auto-defaulted."""
        manifest = {
            "images": [
                {
                    "kind": "iol",
                    "device_id": "iol-xe",
                    "compatible_devices": ["iol-xe"],
                    "is_default": False,
                    "default_for_devices": [],
                }
            ]
        }
        _backfill_single_image_defaults(manifest)
        # iol is not in RUNNABLE_IMAGE_KINDS, so no default assignment
        assert manifest["images"][0]["is_default"] is False

    def test_empty_manifest_noop(self):
        """Empty manifest does not error."""
        manifest = {"images": []}
        _backfill_single_image_defaults(manifest)
        assert manifest["images"] == []


class TestFindImageReference:
    """Tests for find_image_reference()."""

    def test_exact_version_match(self, tmp_path):
        """Returns reference for exact version match."""
        save_manifest(
            {
                "images": [
                    {
                        "id": "docker:ceos:4.28.0F",
                        "kind": "docker",
                        "reference": "ceos:4.28.0F",
                        "filename": "ceos.tar",
                        "device_id": "ceos",
                        "compatible_devices": ["ceos"],
                        "version": "4.28.0F",
                        "is_default": False,
                        "default_for_devices": [],
                    },
                    {
                        "id": "docker:ceos:4.29.0F",
                        "kind": "docker",
                        "reference": "ceos:4.29.0F",
                        "filename": "ceos2.tar",
                        "device_id": "ceos",
                        "compatible_devices": ["ceos"],
                        "version": "4.29.0F",
                        "is_default": True,
                        "default_for_devices": ["ceos"],
                    },
                ]
            }
        )

        result = find_image_reference("ceos", version="4.28.0F")
        assert result == "ceos:4.28.0F"

    def test_default_fallback(self, tmp_path):
        """Returns default image when no version is specified."""
        save_manifest(
            {
                "images": [
                    {
                        "id": "docker:ceos:4.28.0F",
                        "kind": "docker",
                        "reference": "ceos:4.28.0F",
                        "filename": "ceos.tar",
                        "device_id": "ceos",
                        "compatible_devices": ["ceos"],
                        "version": "4.28.0F",
                        "is_default": True,
                        "default_for_devices": ["ceos"],
                    }
                ]
            }
        )

        result = find_image_reference("ceos")
        assert result == "ceos:4.28.0F"

    def test_no_match_returns_none(self, tmp_path):
        """Returns None when no image matches the requested device."""
        save_manifest({"images": []})
        assert find_image_reference("nonexistent") is None

    def test_excludes_iol_kind(self, tmp_path):
        """IOL images are excluded from find_image_reference results."""
        save_manifest(
            {
                "images": [
                    {
                        "id": "iol:test",
                        "kind": "iol",
                        "reference": "/images/test.bin",
                        "filename": "test.bin",
                        "device_id": "iol-xe",
                        "compatible_devices": ["iol-xe"],
                        "is_default": True,
                        "default_for_devices": ["iol-xe"],
                    }
                ]
            }
        )

        assert find_image_reference("iol-xe") is None
