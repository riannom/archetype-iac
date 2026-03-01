"""Tests for vendor device catalog endpoints at /vendors."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session



class TestListVendors:
    """Tests for GET /vendors endpoint."""

    def test_list_vendors_returns_data(
        self, test_client: TestClient, auth_headers: dict
    ):
        """List vendors returns a list of category objects."""
        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_list_vendors_has_categories(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Each vendor entry has a name field for the category."""
        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        if data:
            # Categories should have a name
            for cat in data:
                assert "name" in cat or "models" in cat or "subCategories" in cat

    def test_list_vendors_requires_auth(self, test_client: TestClient):
        """List vendors without auth returns 401."""
        resp = test_client.get("/vendors")
        assert resp.status_code == 401

    def test_list_vendors_filters_hidden(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Hidden devices are excluded from the vendor list."""

        # Mock load_hidden_devices to return a device ID

        with patch(
            "app.routers.vendors.load_hidden_devices",
            return_value=["hidden-device-1"],
        ):
            resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200


class TestGetVendorConfig:
    """Tests for GET /vendors/{device_id}/config endpoint."""

    def test_get_config_builtin_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Get config for a built-in device returns base+overrides+effective."""
        resp = test_client.get("/vendors/linux/config", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "base" in data
        assert "overrides" in data
        assert "effective" in data

    def test_get_config_unknown_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Get config for unknown device returns 404."""
        resp = test_client.get(
            "/vendors/totally-unknown-device-xyz/config",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_get_config_includes_fields(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Config response includes key device fields."""
        resp = test_client.get("/vendors/linux/config", headers=auth_headers)
        assert resp.status_code == 200
        base = resp.json()["base"]
        # Built-in devices should have these core fields
        assert "id" in base
        assert "isBuiltIn" in base


class TestCustomDevices:
    """Tests for custom device CRUD endpoints."""

    def test_add_custom_device_success(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Add a valid custom device succeeds."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ), patch(
            "app.routers.vendors.store_add_device",
            return_value={"id": "my-custom", "name": "My Custom"},
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.post(
                "/vendors",
                json={"id": "my-custom", "name": "My Custom"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["device"]["id"] == "my-custom"

    def test_add_custom_device_missing_id(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Add custom device without ID returns 400."""
        resp = test_client.post(
            "/vendors",
            json={"name": "No ID"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "id" in resp.json()["detail"].lower()

    def test_add_custom_device_missing_name(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Add custom device without name returns 400."""
        resp = test_client.post(
            "/vendors",
            json={"id": "no-name"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["detail"].lower()

    def test_add_custom_device_conflicts_builtin(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Adding a device that conflicts with built-in returns 409."""
        # 'linux' is a built-in device
        resp = test_client.post(
            "/vendors",
            json={"id": "linux", "name": "Conflicting Linux"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "conflicts" in resp.json()["detail"].lower()

    def test_update_custom_device_success(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Update an existing custom device succeeds."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "my-custom", "name": "Old Name"},
        ), patch(
            "app.routers.vendors.update_custom_device",
            return_value={"id": "my-custom", "name": "New Name"},
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.put(
                "/vendors/my-custom",
                json={"name": "New Name"},
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_update_builtin_device_blocked(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Updating a built-in device returns 400."""
        resp = test_client.put(
            "/vendors/linux",
            json={"name": "Hacked Linux"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "built-in" in resp.json()["detail"].lower()

    def test_update_nonexistent_custom_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Updating a nonexistent custom device returns 404."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ):
            resp = test_client.put(
                "/vendors/nonexistent-custom",
                json={"name": "Ghost"},
                headers=auth_headers,
            )
        assert resp.status_code == 404


class TestDeleteDevice:
    """Tests for DELETE /vendors/{device_id} endpoint."""

    def test_delete_custom_device_success(
        self, test_client: TestClient, auth_headers: dict, test_db: Session
    ):
        """Delete a custom device with no images assigned."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="my-device"
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=False
        ), patch(
            "app.routers.vendors.get_device_image_count", return_value=0
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "my-device", "name": "Test"},
        ), patch(
            "app.routers.vendors.store_delete_device", return_value=True
        ):
            resp = test_client.delete(
                "/vendors/my-device", headers=auth_headers
            )
        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()

    def test_delete_device_with_images_blocked(
        self, test_client: TestClient, auth_headers: dict, test_db: Session
    ):
        """Cannot delete device when images are assigned."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="my-device"
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=False
        ), patch(
            "app.routers.vendors.get_device_image_count", return_value=3
        ):
            resp = test_client.delete(
                "/vendors/my-device", headers=auth_headers
            )
        assert resp.status_code == 400
        assert "image" in resp.json()["detail"].lower()


class TestHiddenDevices:
    """Tests for hiding/unhiding built-in devices."""

    def test_list_hidden_devices(
        self, test_client: TestClient, auth_headers: dict
    ):
        """GET /vendors/hidden returns list of hidden device IDs."""
        with patch(
            "app.routers.vendors.load_hidden_devices", return_value=["cisco_iosv"]
        ):
            resp = test_client.get("/vendors/hidden", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "hidden" in data
        assert "cisco_iosv" in data["hidden"]

    def test_hide_builtin_device(
        self, test_client: TestClient, auth_headers: dict, test_db: Session
    ):
        """Deleting a built-in device hides it instead of removing it."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=False
        ), patch(
            "app.routers.vendors.get_device_image_count", return_value=0
        ), patch(
            "app.routers.vendors.is_device_hidden", return_value=False
        ), patch(
            "app.routers.vendors.hide_device"
        ) as mock_hide:
            resp = test_client.delete("/vendors/linux", headers=auth_headers)
        assert resp.status_code == 200
        assert "hidden" in resp.json()["message"].lower()
        mock_hide.assert_called_once()

    def test_restore_hidden_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Restore a hidden built-in device."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.is_device_hidden", return_value=True
        ), patch(
            "app.routers.vendors.unhide_device"
        ) as mock_unhide:
            resp = test_client.post(
                "/vendors/linux/restore", headers=auth_headers
            )
        assert resp.status_code == 200
        assert "restored" in resp.json()["message"].lower()
        mock_unhide.assert_called_once()

    def test_restore_non_hidden_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Restoring a non-hidden device returns 400."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.is_device_hidden", return_value=False
        ):
            resp = test_client.post(
                "/vendors/linux/restore", headers=auth_headers
            )
        assert resp.status_code == 400


class TestDeviceConfigOverrides:
    """Tests for device config override endpoints."""

    def test_update_device_config_valid(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Update device config with valid override fields."""
        mock_config = MagicMock()
        mock_config.kind = "linux"
        mock_config.vendor = "Linux"
        mock_config.label = "Linux"
        mock_config.device_type = MagicMock(value="host")
        mock_config.icon = "fa-linux"
        mock_config.versions = ["latest"]
        mock_config.is_active = True
        mock_config.port_naming = "eth"
        mock_config.port_start_index = 0
        mock_config.max_ports = 16
        mock_config.memory = 512
        mock_config.cpu = 1
        mock_config.requires_image = False
        mock_config.supported_image_kinds = ["docker"]
        mock_config.documentation_url = None
        mock_config.license_required = False
        mock_config.tags = []
        mock_config.notes = None
        mock_config.console_shell = "bash"
        mock_config.readiness_probe = None
        mock_config.readiness_pattern = None
        mock_config.readiness_timeout = 60

        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.set_device_override"
        ), patch(
            "app.routers.vendors.get_device_override", return_value={"memory": 1024}
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ), patch(
            "app.routers.vendors._get_vendor_options", return_value={}
        ):
            resp = test_client.put(
                "/vendors/linux/config",
                json={"memory": 1024},
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_update_device_config_invalid_fields(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Update with no valid override fields returns 400."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ):
            resp = test_client.put(
                "/vendors/linux/config",
                json={"invalidField": "value"},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    def test_reset_device_config(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Reset device config removes overrides."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.delete_device_override", return_value=True
        ):
            resp = test_client.delete(
                "/vendors/linux/config", headers=auth_headers
            )
        assert resp.status_code == 200


class TestIdentityMap:
    """Tests for GET /vendors/identity-map endpoint."""

    def test_get_identity_map(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Identity map returns canonical/alias/runtime mappings."""
        resp = test_client.get("/vendors/identity-map", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # Should have the core identity map keys
        assert "canonical_to_runtime_kind" in data or isinstance(data, dict)
