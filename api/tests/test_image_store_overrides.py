"""Tests for app.image_store.overrides — device override CRUD."""

import pytest

from app.image_store.overrides import (
    delete_device_override,
    get_device_override,
    load_device_overrides,
    save_device_overrides,
    set_device_override,
)


@pytest.fixture(autouse=True)
def _mock_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("app.image_store.overrides.device_overrides_path", lambda: tmp_path / "device_overrides.json")


class TestLoadDeviceOverrides:
    def test_no_file(self):
        assert load_device_overrides() == {}

    def test_with_overrides(self, tmp_path):
        import json
        path = tmp_path / "device_overrides.json"
        path.write_text(json.dumps({"overrides": {"ceos": {"maxPorts": 32}}}))
        overrides = load_device_overrides()
        assert overrides["ceos"]["maxPorts"] == 32


class TestSaveDeviceOverrides:
    def test_save_and_load(self):
        save_device_overrides({"ceos": {"memory": 4096}})
        loaded = load_device_overrides()
        assert loaded["ceos"]["memory"] == 4096


class TestGetDeviceOverride:
    def test_exists(self):
        save_device_overrides({"ceos": {"memory": 4096}})
        assert get_device_override("ceos") == {"memory": 4096}

    def test_not_found(self):
        assert get_device_override("unknown") is None


class TestSetDeviceOverride:
    def test_create_new(self):
        result = set_device_override("ceos", {"memory": 4096})
        assert result == {"memory": 4096}
        assert get_device_override("ceos") == {"memory": 4096}

    def test_update_existing(self):
        set_device_override("ceos", {"memory": 4096})
        result = set_device_override("ceos", {"maxPorts": 32})
        assert result == {"memory": 4096, "maxPorts": 32}

    def test_overwrite_field(self):
        set_device_override("ceos", {"memory": 4096})
        result = set_device_override("ceos", {"memory": 8192})
        assert result["memory"] == 8192


class TestDeleteDeviceOverride:
    def test_delete_existing(self):
        set_device_override("ceos", {"memory": 4096})
        assert delete_device_override("ceos") is True
        assert get_device_override("ceos") is None

    def test_delete_not_found(self):
        assert delete_device_override("unknown") is False
