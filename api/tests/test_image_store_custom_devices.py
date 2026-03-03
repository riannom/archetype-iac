"""Tests for app.image_store.custom_devices — CRUD, hidden devices, rules, orphan cleanup."""

import json
from unittest.mock import patch

import pytest

from app.image_store.custom_devices import (
    _display_name_from_device_id,
    _infer_dynamic_custom_device_metadata,
    add_custom_device,
    cleanup_orphaned_custom_devices,
    delete_custom_device,
    find_custom_device,
    hide_device,
    is_device_hidden,
    load_custom_devices,
    load_hidden_devices,
    load_rules,
    save_custom_devices,
    save_hidden_devices,
    unhide_device,
    update_custom_device,
)


@pytest.fixture(autouse=True)
def _mock_image_store_path(tmp_path, monkeypatch):
    """Redirect all file I/O to a temp directory."""
    monkeypatch.setattr("app.image_store.paths.ensure_image_store", lambda: tmp_path)
    monkeypatch.setattr("app.image_store.paths.image_store_root", lambda: tmp_path)
    monkeypatch.setattr("app.image_store.custom_devices.rules_path", lambda: tmp_path / "rules.json")
    monkeypatch.setattr("app.image_store.custom_devices.custom_devices_path", lambda: tmp_path / "custom_devices.json")
    monkeypatch.setattr("app.image_store.custom_devices.hidden_devices_path", lambda: tmp_path / "hidden_devices.json")


@pytest.fixture(autouse=True)
def _mock_vendor_check(monkeypatch):
    """Prevent VENDOR_CONFIGS import errors during custom device operations."""
    monkeypatch.setattr(
        "app.image_store.custom_devices.validate_minimum_hardware",
        lambda device_id, memory, cpu: None,
    )


# ---------------------------------------------------------------------------
# load_rules
# ---------------------------------------------------------------------------
class TestLoadRules:
    def test_no_file(self):
        assert load_rules() == []

    def test_with_rules(self, tmp_path):
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({"rules": [{"pattern": "test", "device_id": "dev1"}]}))
        rules = load_rules()
        assert len(rules) == 1
        assert rules[0]["device_id"] == "dev1"


# ---------------------------------------------------------------------------
# Hidden devices
# ---------------------------------------------------------------------------
class TestHiddenDevices:
    def test_load_empty(self):
        assert load_hidden_devices() == []

    def test_save_and_load(self, tmp_path):
        save_hidden_devices(["ceos", "srlinux"])
        assert load_hidden_devices() == ["ceos", "srlinux"]

    def test_hide_device(self):
        assert hide_device("ceos") is True
        assert is_device_hidden("ceos") is True

    def test_hide_device_already_hidden(self):
        hide_device("ceos")
        assert hide_device("ceos") is False

    def test_unhide_device(self):
        hide_device("ceos")
        assert unhide_device("ceos") is True
        assert is_device_hidden("ceos") is False

    def test_unhide_not_hidden(self):
        assert unhide_device("ceos") is False

    def test_is_device_hidden_false(self):
        assert is_device_hidden("unknown") is False


# ---------------------------------------------------------------------------
# Custom device CRUD
# ---------------------------------------------------------------------------
class TestCustomDeviceCrud:
    def _make_device(self, device_id="test-device", **kwargs):
        d = {"id": device_id, "name": f"Test {device_id}"}
        d.update(kwargs)
        return d

    def test_load_empty(self):
        assert load_custom_devices() == []

    def test_save_and_load(self):
        devices = [self._make_device()]
        save_custom_devices(devices)
        loaded = load_custom_devices()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "test-device"

    def test_add_custom_device(self, monkeypatch):
        # Prevent vendor shadowing check
        monkeypatch.setattr(
            "app.image_store.custom_devices.add_custom_device.__module__",
            "app.image_store.custom_devices",
        )
        device = add_custom_device(self._make_device("my-dev"))
        assert device["id"] == "my-dev"
        assert device["isCustom"] is True
        assert device["type"] == "container"  # default
        assert device["memory"] == 1024  # default
        assert device["maxPorts"] == 8  # default

    def test_add_duplicate_raises(self):
        add_custom_device(self._make_device("dup"))
        with pytest.raises(ValueError, match="already exists"):
            add_custom_device(self._make_device("dup"))

    def test_find_custom_device(self):
        add_custom_device(self._make_device("finder"))
        found = find_custom_device("finder")
        assert found is not None
        assert found["id"] == "finder"

    def test_find_missing_returns_none(self):
        assert find_custom_device("nonexistent") is None

    def test_update_custom_device(self):
        add_custom_device(self._make_device("updatable"))
        result = update_custom_device("updatable", {"memory": 4096})
        assert result is not None
        assert result["memory"] == 4096

    def test_update_prevents_id_change(self):
        add_custom_device(self._make_device("no-change"))
        result = update_custom_device("no-change", {"id": "hacked", "memory": 2048})
        assert result["id"] == "no-change"
        assert result["memory"] == 2048

    def test_update_prevents_isCustom_change(self):
        add_custom_device(self._make_device("stay-custom"))
        result = update_custom_device("stay-custom", {"isCustom": False})
        assert result["isCustom"] is True

    def test_update_missing_returns_none(self):
        assert update_custom_device("ghost", {"memory": 1024}) is None

    def test_delete_custom_device(self):
        add_custom_device(self._make_device("deletable"))
        deleted = delete_custom_device("deletable")
        assert deleted is not None
        assert deleted["id"] == "deletable"
        assert find_custom_device("deletable") is None

    def test_delete_missing_returns_none(self):
        assert delete_custom_device("ghost") is None


# ---------------------------------------------------------------------------
# _infer_dynamic_custom_device_metadata
# ---------------------------------------------------------------------------
class TestInferDynamicMetadata:
    def test_empty(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("")
        assert dev_type == "container"
        assert category == "Compute"

    def test_firewall(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("asav-firewall")
        assert dev_type == "firewall"
        assert category == "Security"

    def test_switch(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("n9k-switch")
        assert dev_type == "switch"
        assert category == "Network"
        assert sub == "Switches"

    def test_router(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("xrv-router")
        assert dev_type == "router"
        assert category == "Network"
        assert sub == "Routers"

    def test_host(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("windows-server")
        assert dev_type == "host"
        assert category == "Compute"

    def test_unknown(self):
        dev_type, icon, category, sub = _infer_dynamic_custom_device_metadata("mystery_box")
        assert dev_type == "container"


# ---------------------------------------------------------------------------
# _display_name_from_device_id
# ---------------------------------------------------------------------------
class TestDisplayNameFromDeviceId:
    def test_dashes(self):
        assert _display_name_from_device_id("cisco-csr-1000v") == "Cisco Csr 1000V"

    def test_underscores(self):
        assert _display_name_from_device_id("nokia_srlinux") == "Nokia Srlinux"

    def test_empty(self):
        assert _display_name_from_device_id("") == "Custom Device"

    def test_mixed_separators(self):
        assert _display_name_from_device_id("my_custom-device") == "My Custom Device"


# ---------------------------------------------------------------------------
# cleanup_orphaned_custom_devices
# ---------------------------------------------------------------------------
class TestCleanupOrphanedDevices:
    def test_removes_orphans(self):
        add_custom_device({"id": "orphan-dev", "name": "Orphan"})
        with patch("app.image_store.manifest.load_manifest", return_value={"images": []}), \
             patch("app.image_store.aliases.image_matches_device", return_value=False):
            removed = cleanup_orphaned_custom_devices()
        assert "orphan-dev" in removed
        assert find_custom_device("orphan-dev") is None

    def test_keeps_devices_with_images(self):
        add_custom_device({"id": "has-image", "name": "Has Image"})
        with patch("app.image_store.manifest.load_manifest",
                    return_value={"images": [{"device_id": "has-image"}]}), \
             patch("app.image_store.aliases.image_matches_device",
                   side_effect=lambda img, did: did == "has-image"):
            removed = cleanup_orphaned_custom_devices()
        assert "has-image" not in removed
        assert find_custom_device("has-image") is not None

    def test_skips_non_custom_devices(self):
        save_custom_devices([{"id": "vendor-dev", "name": "Vendor", "isCustom": False}])
        with patch("app.image_store.manifest.load_manifest", return_value={"images": []}), \
             patch("app.image_store.aliases.image_matches_device", return_value=False):
            removed = cleanup_orphaned_custom_devices()
        assert "vendor-dev" not in removed
