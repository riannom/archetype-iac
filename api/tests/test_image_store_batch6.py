"""Batch 6: Image store gap-fill tests.

Covers untested helper functions in image_store.py:
- detect_iol_device_type
- detect_qcow2_device_type
- get_image_provider
- hide_device / unhide_device / is_device_hidden
- device override CRUD
- cleanup_orphaned_custom_devices
- find_custom_device
- add_custom_device (duplicate protection)
- _display_name_from_device_id
- _infer_dynamic_custom_device_metadata
- _maybe_backfill_specific_linux_device
- _maybe_backfill_vjunos_evolved_device
- _extract_version
- image_matches_device
- normalize_default_device_scope_id
- canonicalize_device_ids
"""
from __future__ import annotations

import json



# ---------------------------------------------------------------------------
# detect_iol_device_type
# ---------------------------------------------------------------------------

class TestDetectIolDeviceType:
    def test_l2_explicit(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("i86bi-linux-l2-adventerprisek9-15.bin") == "iol-l2"

    def test_l3_explicit(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("i86bi-linux-l3-adventerprisek9-15.6.1T.bin") == "iol-xe"

    def test_l2_ioll2_variant(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("ioll2-linux.bin") == "iol-l2"

    def test_l2_underscore_variant(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("iol_l2.bin") == "iol-l2"

    def test_generic_iol_returns_l3(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("iol-something.bin") == "iol-xe"

    def test_unrelated_filename_returns_none(self):
        from app.image_store import detect_iol_device_type
        assert detect_iol_device_type("ceos-4.28.0F.tar") is None


# ---------------------------------------------------------------------------
# get_image_provider
# ---------------------------------------------------------------------------

class TestGetImageProvider:
    def test_qcow2_returns_libvirt(self):
        from app.image_store import get_image_provider
        assert get_image_provider("iosv-15.9.qcow2") == "libvirt"

    def test_img_returns_libvirt(self):
        from app.image_store import get_image_provider
        assert get_image_provider("vios-adventerprisek9.img") == "libvirt"

    def test_docker_tag_returns_docker(self):
        from app.image_store import get_image_provider
        assert get_image_provider("ceos:4.28.0F") == "docker"

    def test_none_returns_docker(self):
        from app.image_store import get_image_provider
        assert get_image_provider(None) == "docker"

    def test_empty_returns_docker(self):
        from app.image_store import get_image_provider
        assert get_image_provider("") == "docker"


# ---------------------------------------------------------------------------
# Hidden devices CRUD
# ---------------------------------------------------------------------------

class TestHiddenDevices:
    def test_hide_device(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "hidden_devices_path", lambda: tmp_path / "hidden_devices.json")

        assert image_store.hide_device("ceos") is True
        assert image_store.is_device_hidden("ceos") is True

    def test_hide_device_already_hidden(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "hidden_devices_path", lambda: tmp_path / "hidden_devices.json")

        image_store.hide_device("ceos")
        assert image_store.hide_device("ceos") is False

    def test_unhide_device(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "hidden_devices_path", lambda: tmp_path / "hidden_devices.json")

        image_store.hide_device("ceos")
        assert image_store.unhide_device("ceos") is True
        assert image_store.is_device_hidden("ceos") is False

    def test_unhide_not_hidden(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "hidden_devices_path", lambda: tmp_path / "hidden_devices.json")

        assert image_store.unhide_device("linux") is False

    def test_load_hidden_missing_file(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "hidden_devices_path", lambda: tmp_path / "missing.json")
        assert image_store.load_hidden_devices() == []


# ---------------------------------------------------------------------------
# Device override CRUD
# ---------------------------------------------------------------------------

class TestDeviceOverrides:
    def _setup(self, tmp_path, monkeypatch):
        from app import image_store
        path = tmp_path / "device_overrides.json"
        monkeypatch.setattr(image_store, "device_overrides_path", lambda: path)
        return image_store

    def test_load_overrides_missing_file(self, tmp_path, monkeypatch):
        store = self._setup(tmp_path, monkeypatch)
        assert store.load_device_overrides() == {}

    def test_set_and_get_override(self, tmp_path, monkeypatch):
        store = self._setup(tmp_path, monkeypatch)
        result = store.set_device_override("ceos", {"memory": 4096})
        assert result == {"memory": 4096}
        assert store.get_device_override("ceos") == {"memory": 4096}

    def test_update_existing_override(self, tmp_path, monkeypatch):
        store = self._setup(tmp_path, monkeypatch)
        store.set_device_override("ceos", {"memory": 4096})
        store.set_device_override("ceos", {"cpu": 2})
        result = store.get_device_override("ceos")
        assert result == {"memory": 4096, "cpu": 2}

    def test_delete_override(self, tmp_path, monkeypatch):
        store = self._setup(tmp_path, monkeypatch)
        store.set_device_override("ceos", {"memory": 4096})
        assert store.delete_device_override("ceos") is True
        assert store.get_device_override("ceos") is None

    def test_delete_nonexistent_override(self, tmp_path, monkeypatch):
        store = self._setup(tmp_path, monkeypatch)
        assert store.delete_device_override("iosv") is False


# ---------------------------------------------------------------------------
# _display_name_from_device_id
# ---------------------------------------------------------------------------

class TestDisplayNameFromDeviceId:
    def test_underscore_separated(self):
        from app.image_store import _display_name_from_device_id
        assert _display_name_from_device_id("cisco_iosv") == "Cisco Iosv"

    def test_hyphen_separated(self):
        from app.image_store import _display_name_from_device_id
        assert _display_name_from_device_id("arista-ceos") == "Arista Ceos"

    def test_empty_string(self):
        from app.image_store import _display_name_from_device_id
        assert _display_name_from_device_id("") == "Custom Device"

    def test_whitespace_only(self):
        from app.image_store import _display_name_from_device_id
        assert _display_name_from_device_id("  ") == "Custom Device"


# ---------------------------------------------------------------------------
# _infer_dynamic_custom_device_metadata
# ---------------------------------------------------------------------------

class TestInferDynamicCustomDeviceMetadata:
    def test_firewall_detected(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, icon, category, _ = _infer_dynamic_custom_device_metadata("cisco-ftdv")
        assert dev_type == "firewall"
        assert category == "Security"

    def test_switch_detected(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("nxos-switch")
        assert dev_type == "switch"
        assert sub == "Switches"

    def test_router_detected(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, _, _, sub = _infer_dynamic_custom_device_metadata("junos-router")
        assert dev_type == "router"
        assert sub == "Routers"

    def test_host_detected(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, _, category, _ = _infer_dynamic_custom_device_metadata("windows-server")
        assert dev_type == "host"
        assert category == "Compute"

    def test_unknown_returns_container(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, _, _, _ = _infer_dynamic_custom_device_metadata("something-else")
        assert dev_type == "container"

    def test_empty_returns_container(self):
        from app.image_store import _infer_dynamic_custom_device_metadata
        dev_type, _, _, _ = _infer_dynamic_custom_device_metadata("")
        assert dev_type == "container"


# ---------------------------------------------------------------------------
# _maybe_backfill_specific_linux_device
# ---------------------------------------------------------------------------

class TestMaybeBackfillSpecificLinuxDevice:
    def test_frr_backfill(self):
        from app.image_store import _maybe_backfill_specific_linux_device
        image = {"device_id": "linux", "id": "docker:frr/frr:latest", "reference": "frr/frr:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "frr"

    def test_haproxy_backfill(self):
        from app.image_store import _maybe_backfill_specific_linux_device
        image = {"device_id": "linux", "id": "docker:haproxy:2.4", "reference": "haproxy:2.4"}
        assert _maybe_backfill_specific_linux_device(image) == "haproxy"

    def test_alpine_backfill(self):
        from app.image_store import _maybe_backfill_specific_linux_device
        image = {"device_id": "linux", "id": "", "reference": "alpine:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "alpine"

    def test_non_linux_unchanged(self):
        from app.image_store import _maybe_backfill_specific_linux_device
        image = {"device_id": "ceos", "reference": "ceos:4.28.0F"}
        assert _maybe_backfill_specific_linux_device(image) == "ceos"

    def test_generic_linux_unchanged(self):
        from app.image_store import _maybe_backfill_specific_linux_device
        image = {"device_id": "linux", "id": "", "reference": "custom-image:latest"}
        assert _maybe_backfill_specific_linux_device(image) == "linux"


# ---------------------------------------------------------------------------
# _maybe_backfill_vjunos_evolved_device
# ---------------------------------------------------------------------------

class TestMaybeBackfillVjunosEvolved:
    def test_evolved_backfill(self):
        from app.image_store import _maybe_backfill_vjunos_evolved_device
        image = {"id": "vjunos-evolved-24.2R1.qcow2", "reference": "", "filename": ""}
        result = _maybe_backfill_vjunos_evolved_device(image, "juniper_vjunosrouter")
        assert result == "juniper_vjunosevolved"

    def test_non_router_unchanged(self):
        from app.image_store import _maybe_backfill_vjunos_evolved_device
        image = {"id": "vjunos-evolved-24.2R1.qcow2", "reference": "", "filename": ""}
        result = _maybe_backfill_vjunos_evolved_device(image, "ceos")
        assert result == "ceos"

    def test_router_without_evolved_keyword(self):
        from app.image_store import _maybe_backfill_vjunos_evolved_device
        image = {"id": "vjunos-router-24.2R1.qcow2", "reference": "", "filename": ""}
        result = _maybe_backfill_vjunos_evolved_device(image, "juniper_vjunosrouter")
        assert result == "juniper_vjunosrouter"


# ---------------------------------------------------------------------------
# _extract_version
# ---------------------------------------------------------------------------

class TestExtractVersion:
    def test_semver(self):
        from app.image_store import _extract_version
        assert _extract_version("ceos-4.28.0F.tar") == "4.28.0"

    def test_no_version(self):
        from app.image_store import _extract_version
        assert _extract_version("linux-image.tar") is None

    def test_complex_version(self):
        from app.image_store import _extract_version
        result = _extract_version("iosv-15.9.3M.qcow2")
        assert result is not None
        assert result.startswith("15.9")


# ---------------------------------------------------------------------------
# normalize_default_device_scope_id
# ---------------------------------------------------------------------------

class TestNormalizeDefaultDeviceScopeId:
    def test_normalizes_case(self):
        from app.image_store import normalize_default_device_scope_id
        assert normalize_default_device_scope_id("CEOS") == "ceos"

    def test_strips_whitespace(self):
        from app.image_store import normalize_default_device_scope_id
        assert normalize_default_device_scope_id("  ceos  ") == "ceos"

    def test_none_returns_none(self):
        from app.image_store import normalize_default_device_scope_id
        assert normalize_default_device_scope_id(None) is None

    def test_empty_returns_none(self):
        from app.image_store import normalize_default_device_scope_id
        assert normalize_default_device_scope_id("") is None


# ---------------------------------------------------------------------------
# canonicalize_device_ids
# ---------------------------------------------------------------------------

class TestCanonicalizeDeviceIds:
    def test_empty_list(self):
        from app.image_store import canonicalize_device_ids
        assert canonicalize_device_ids([]) == []

    def test_none_input(self):
        from app.image_store import canonicalize_device_ids
        assert canonicalize_device_ids(None) == []

    def test_deduplicates(self):
        from app.image_store import canonicalize_device_ids
        # Both "ceos" and "CEOS" should resolve to the same canonical ID
        result = canonicalize_device_ids(["ceos", "CEOS"])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# image_matches_device
# ---------------------------------------------------------------------------

class TestImageMatchesDevice:
    def test_direct_match(self):
        from app.image_store import image_matches_device
        image = {"device_id": "ceos", "compatible_devices": ["ceos"]}
        assert image_matches_device(image, "ceos") is True

    def test_no_match(self):
        from app.image_store import image_matches_device
        image = {"device_id": "ceos", "compatible_devices": ["ceos"]}
        assert image_matches_device(image, "iosv") is False

    def test_compatible_device_match(self):
        from app.image_store import image_matches_device
        image = {"device_id": "ceos", "compatible_devices": ["ceos", "eos"]}
        assert image_matches_device(image, "eos") is True

    def test_empty_device_id(self):
        from app.image_store import image_matches_device
        assert image_matches_device({"device_id": None}, "") is False


# ---------------------------------------------------------------------------
# cleanup_orphaned_custom_devices
# ---------------------------------------------------------------------------

class TestCleanupOrphanedCustomDevices:
    def test_removes_orphans(self, tmp_path, monkeypatch):
        from app import image_store

        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")
        # Mock load_manifest to return empty images (bypasses DB path)
        monkeypatch.setattr(image_store, "load_manifest", lambda: {"images": []})

        # Write custom device
        (tmp_path / "custom_devices.json").write_text(json.dumps({
            "devices": [{"id": "orphan-dev", "isCustom": True, "name": "Orphan"}]
        }))

        removed = image_store.cleanup_orphaned_custom_devices()
        assert "orphan-dev" in removed

    def test_keeps_devices_with_images(self, tmp_path, monkeypatch):
        from app import image_store

        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")
        # Mock load_manifest with matching image
        monkeypatch.setattr(image_store, "load_manifest", lambda: {
            "images": [{"device_id": "my-device", "compatible_devices": ["my-device"]}]
        })

        # Write matching custom device
        (tmp_path / "custom_devices.json").write_text(json.dumps({
            "devices": [{"id": "my-device", "isCustom": True, "name": "My Device"}]
        }))

        removed = image_store.cleanup_orphaned_custom_devices()
        assert removed == []


# ---------------------------------------------------------------------------
# find_custom_device
# ---------------------------------------------------------------------------

class TestFindCustomDevice:
    def test_finds_existing(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")

        (tmp_path / "custom_devices.json").write_text(json.dumps({
            "devices": [{"id": "test-dev", "name": "Test"}]
        }))

        result = image_store.find_custom_device("test-dev")
        assert result is not None
        assert result["id"] == "test-dev"

    def test_returns_none_not_found(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")

        (tmp_path / "custom_devices.json").write_text(json.dumps({"devices": []}))
        assert image_store.find_custom_device("missing") is None

    def test_returns_none_missing_file(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "missing.json")
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        assert image_store.find_custom_device("anything") is None


# ---------------------------------------------------------------------------
# delete_custom_device
# ---------------------------------------------------------------------------

class TestDeleteCustomDevice:
    def test_deletes_existing(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")

        (tmp_path / "custom_devices.json").write_text(json.dumps({
            "devices": [{"id": "test-dev", "name": "Test"}]
        }))

        result = image_store.delete_custom_device("test-dev")
        assert result is not None
        assert result["id"] == "test-dev"
        assert image_store.find_custom_device("test-dev") is None

    def test_returns_none_not_found(self, tmp_path, monkeypatch):
        from app import image_store
        monkeypatch.setattr(image_store, "ensure_image_store", lambda: tmp_path)
        monkeypatch.setattr(image_store, "custom_devices_path", lambda: tmp_path / "custom_devices.json")

        (tmp_path / "custom_devices.json").write_text(json.dumps({"devices": []}))
        assert image_store.delete_custom_device("missing") is None
