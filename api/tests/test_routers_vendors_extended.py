"""Extended tests for vendor device catalog endpoints at /vendors.

Covers gaps not addressed by test_routers_vendors.py and
test_routers_vendors_extra.py: unauthenticated catalog-seeded access,
custom device category merging, hardware validation, duplicate handling,
edge cases in delete/restore/config flows, and more.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# GET /vendors — unauthenticated access
# ---------------------------------------------------------------------------


class TestListVendorsUnauthenticated:
    """GET /vendors without credentials — catalog-seeded vs unseeded behavior."""

    def test_list_vendors_no_auth_catalog_seeded_succeeds(
        self, test_client: TestClient, monkeypatch
    ):
        """Unauthenticated access is allowed when catalog is seeded."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [{"name": "Network", "models": [{"id": "linux"}]}],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        with patch("app.routers.vendors.catalog_is_seeded", return_value=True), patch(
            "app.routers.vendors._using_default_vendor_registry_loader",
            return_value=True,
        ):
            resp = test_client.get("/vendors")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_vendors_no_auth_catalog_not_seeded_returns_401(
        self, test_client: TestClient
    ):
        """Unauthenticated access is blocked when catalog is not seeded."""
        with patch("app.routers.vendors.catalog_is_seeded", return_value=False), patch(
            "app.routers.vendors._using_default_vendor_registry_loader",
            return_value=True,
        ):
            resp = test_client.get("/vendors")
        assert resp.status_code == 401

    def test_list_vendors_no_auth_non_default_loader_allowed(
        self, test_client: TestClient, monkeypatch
    ):
        """When a custom loader is installed, unauthenticated access is always allowed."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [{"name": "Network", "models": [{"id": "linux"}]}],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        with patch(
            "app.routers.vendors._using_default_vendor_registry_loader",
            return_value=False,
        ):
            resp = test_client.get("/vendors")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /vendors — custom device category merging
# ---------------------------------------------------------------------------


class TestListVendorsCustomDeviceMerging:
    """Custom devices are merged into the correct category in GET /vendors."""

    def test_custom_device_added_to_new_category(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Custom devices whose category does not exist become a new top-level category."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [{"name": "Network", "models": [{"id": "linux"}]}],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr(
            "app.image_store.load_custom_devices",
            lambda: [{"id": "my-fw", "name": "My Firewall", "category": "Security"}],
        )

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        category_names = [c.get("name") for c in data]
        assert "Security" in category_names
        security_cat = next(c for c in data if c.get("name") == "Security")
        model_ids = [m["id"] for m in security_cat.get("models", [])]
        assert "my-fw" in model_ids

    def test_custom_device_added_to_existing_flat_category(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Custom device merged into existing flat (non-subcategory) category."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [
                {
                    "name": "Compute",
                    "models": [{"id": "linux"}],
                }
            ],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr(
            "app.image_store.load_custom_devices",
            lambda: [
                {"id": "my-vm", "name": "My VM", "category": "Compute"}
            ],
        )

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        compute_cat = next((c for c in data if c.get("name") == "Compute"), None)
        assert compute_cat is not None
        model_ids = [m["id"] for m in compute_cat.get("models", [])]
        assert "linux" in model_ids
        assert "my-vm" in model_ids

    def test_custom_device_added_to_existing_subcategory_category(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Custom device creates a 'Custom' subcategory when category uses subCategories."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [
                {
                    "name": "Network",
                    "subCategories": [
                        {"name": "Routers", "models": [{"id": "linux"}]}
                    ],
                }
            ],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr(
            "app.image_store.load_custom_devices",
            lambda: [{"id": "my-router", "name": "My Router", "category": "Network"}],
        )

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        network_cat = next((c for c in data if c.get("name") == "Network"), None)
        assert network_cat is not None
        subcats = network_cat.get("subCategories", [])
        custom_subcat = next(
            (s for s in subcats if s.get("name") == "Custom"), None
        )
        assert custom_subcat is not None
        model_ids = [m["id"] for m in custom_subcat.get("models", [])]
        assert "my-router" in model_ids

    def test_custom_device_added_to_existing_custom_subcategory(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Custom device appended to existing 'Custom' subcategory."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [
                {
                    "name": "Network",
                    "subCategories": [
                        {
                            "name": "Custom",
                            "models": [{"id": "existing-custom"}],
                        }
                    ],
                }
            ],
        )
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr(
            "app.image_store.load_custom_devices",
            lambda: [
                {"id": "new-custom", "name": "New Custom", "category": "Network"}
            ],
        )

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        network_cat = next((c for c in data if c.get("name") == "Network"), None)
        assert network_cat is not None
        custom_subcat = next(
            (s for s in network_cat.get("subCategories", []) if s["name"] == "Custom"),
            None,
        )
        assert custom_subcat is not None
        model_ids = [m["id"] for m in custom_subcat["models"]]
        assert "existing-custom" in model_ids
        assert "new-custom" in model_ids

    def test_hidden_device_removes_empty_subcategory(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Subcategories that become empty after hiding are removed."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [
                {
                    "name": "Network",
                    "subCategories": [
                        {"name": "Switches", "models": [{"id": "hidden-switch"}]},
                        {"name": "Routers", "models": [{"id": "linux"}]},
                    ],
                }
            ],
        )
        monkeypatch.setattr(
            "app.image_store.load_hidden_devices", lambda: ["hidden-switch"]
        )
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        network_cat = next((c for c in data if c.get("name") == "Network"), None)
        assert network_cat is not None
        subcat_names = [s["name"] for s in network_cat.get("subCategories", [])]
        assert "Switches" not in subcat_names
        assert "Routers" in subcat_names

    def test_all_models_hidden_removes_entire_category(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """A category is removed entirely when all its models are hidden."""
        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [
                {"name": "Legacy", "models": [{"id": "old-device"}]},
                {"name": "Network", "models": [{"id": "linux"}]},
            ],
        )
        monkeypatch.setattr(
            "app.image_store.load_hidden_devices", lambda: ["old-device"]
        )
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        category_names = [c.get("name") for c in data]
        assert "Legacy" not in category_names
        assert "Network" in category_names

    def test_compatibility_aliases_attached_to_models(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Models are enriched with compatibilityAliases and runtimeKind from identity map."""

        class FakeConfig:
            kind = "linux"
            aliases = ["debian", "ubuntu"]

        monkeypatch.setattr(
            "agent.vendors.get_vendors_for_ui",
            lambda: [{"name": "Compute", "models": [{"id": "linux"}]}],
        )
        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"linux": FakeConfig()})
        monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])
        monkeypatch.setattr(
            "app.image_store.get_image_compatibility_aliases", lambda: {}
        )

        resp = test_client.get("/vendors", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        compute = next((c for c in data if c.get("name") == "Compute"), None)
        assert compute is not None
        linux_model = next(
            (m for m in compute.get("models", []) if m["id"] == "linux"), None
        )
        assert linux_model is not None
        assert "compatibilityAliases" in linux_model
        assert "runtimeKind" in linux_model
        assert set(linux_model["compatibilityAliases"]) == {"debian", "ubuntu"}


# ---------------------------------------------------------------------------
# POST /vendors — add custom device edge cases
# ---------------------------------------------------------------------------


class TestAddCustomDeviceEdgeCases:
    """Edge cases for POST /vendors."""

    def test_add_custom_device_duplicate_returns_409(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Adding a device that already exists as a custom device returns 409."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "existing-device", "name": "Existing"},
        ):
            resp = test_client.post(
                "/vendors",
                json={"id": "existing-device", "name": "Existing"},
                headers=auth_headers,
            )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

    def test_add_custom_device_hardware_validation_failure(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Adding a Cat9k device with insufficient memory returns 400."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch("app.routers.vendors.find_custom_device", return_value=None):
            resp = test_client.post(
                "/vendors",
                json={
                    "id": "cat9000v-uadp",
                    "name": "Cat9k UADP",
                    "memory": 512,
                    "cpu": 1,
                },
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "memory" in resp.json()["detail"].lower()

    def test_add_custom_device_store_raises_already_exists(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When store_add_device raises 'already exists' ValueError it returns 409."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ), patch(
            "app.routers.vendors.store_add_device",
            side_effect=ValueError("Device already exists"),
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.post(
                "/vendors",
                json={"id": "race-device", "name": "Race Device"},
                headers=auth_headers,
            )
        assert resp.status_code == 409

    def test_add_custom_device_store_raises_other_value_error(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When store_add_device raises a generic ValueError it returns 400."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ), patch(
            "app.routers.vendors.store_add_device",
            side_effect=ValueError("Invalid device format"),
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.post(
                "/vendors",
                json={"id": "bad-device", "name": "Bad Device"},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    def test_add_custom_device_requires_auth(self, test_client: TestClient):
        """POST /vendors without auth returns 401."""
        resp = test_client.post(
            "/vendors", json={"id": "my-device", "name": "My Device"}
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /vendors/{device_id} — additional edge cases
# ---------------------------------------------------------------------------


class TestDeleteDeviceEdgeCases:
    """Edge cases for DELETE /vendors/{device_id}."""

    def test_delete_builtin_already_hidden_returns_400(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Hiding an already-hidden built-in device returns 400."""
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
            "app.routers.vendors.is_device_hidden", return_value=True
        ):
            resp = test_client.delete("/vendors/linux", headers=auth_headers)
        assert resp.status_code == 400
        assert "already hidden" in resp.json()["detail"].lower()

    def test_delete_nonexistent_custom_device_returns_404(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Deleting a device that is neither builtin nor custom returns 404."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value=None
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=False
        ), patch(
            "app.routers.vendors.get_device_image_count", return_value=0
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=None
        ):
            resp = test_client.delete(
                "/vendors/ghost-device-xyz", headers=auth_headers
            )
        assert resp.status_code == 404

    def test_delete_custom_device_store_returns_false_gives_404(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When store_delete_device returns False it returns 404."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value=None
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=False
        ), patch(
            "app.routers.vendors.get_device_image_count", return_value=0
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "stale-device", "name": "Stale"},
        ), patch(
            "app.routers.vendors.store_delete_device", return_value=False
        ):
            resp = test_client.delete(
                "/vendors/stale-device", headers=auth_headers
            )
        assert resp.status_code == 404

    def test_delete_uses_catalog_image_count_when_seeded(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When catalog is seeded, count_catalog_images_for_device is used instead."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="my-device"
        ), patch(
            "app.routers.vendors.catalog_is_seeded", return_value=True
        ), patch(
            "app.routers.vendors.count_catalog_images_for_device", return_value=2
        ) as mock_catalog_count, patch(
            "app.routers.vendors.get_device_image_count"
        ) as mock_manifest_count:
            resp = test_client.delete(
                "/vendors/my-device", headers=auth_headers
            )
        assert resp.status_code == 400
        mock_catalog_count.assert_called_once()
        mock_manifest_count.assert_not_called()

    def test_delete_requires_auth(self, test_client: TestClient):
        """DELETE /vendors/{id} without auth returns 401."""
        resp = test_client.delete("/vendors/linux")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /vendors/{device_id}/restore — additional edge cases
# ---------------------------------------------------------------------------


class TestRestoreDeviceEdgeCases:
    """Edge cases for POST /vendors/{device_id}/restore."""

    def test_restore_custom_device_returns_400(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Restoring a custom (non-builtin) device returns 400."""
        with patch("app.routers.vendors.get_config_by_device", return_value=None):
            resp = test_client.post(
                "/vendors/my-custom/restore", headers=auth_headers
            )
        assert resp.status_code == 400
        assert "built-in" in resp.json()["detail"].lower()

    def test_restore_requires_auth(self, test_client: TestClient):
        """POST /vendors/{id}/restore without auth returns 401."""
        resp = test_client.post("/vendors/linux/restore")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /vendors/hidden — additional cases
# ---------------------------------------------------------------------------


class TestListHiddenDevices:
    """Tests for GET /vendors/hidden."""

    def test_list_hidden_devices_empty(
        self, test_client: TestClient, auth_headers: dict
    ):
        """GET /vendors/hidden returns empty list when no devices are hidden."""
        with patch("app.routers.vendors.load_hidden_devices", return_value=[]):
            resp = test_client.get("/vendors/hidden", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"hidden": []}

    def test_list_hidden_devices_requires_auth(self, test_client: TestClient):
        """GET /vendors/hidden without auth returns 401."""
        resp = test_client.get("/vendors/hidden")
        assert resp.status_code == 401

    def test_list_hidden_devices_multiple(
        self, test_client: TestClient, auth_headers: dict
    ):
        """GET /vendors/hidden returns all hidden device IDs."""
        hidden = ["cisco_iosv", "cisco_csr1000v", "legacy_device"]
        with patch("app.routers.vendors.load_hidden_devices", return_value=hidden):
            resp = test_client.get("/vendors/hidden", headers=auth_headers)
        assert resp.status_code == 200
        assert set(resp.json()["hidden"]) == set(hidden)


# ---------------------------------------------------------------------------
# PUT /vendors/{device_id} — update custom device edge cases
# ---------------------------------------------------------------------------


class TestUpdateCustomDeviceEdgeCases:
    """Edge cases for PUT /vendors/{device_id}."""

    def test_update_custom_device_hardware_validation_failure(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Update with insufficient Cat9k memory returns 400."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "cat9000v-uadp", "name": "Cat9k"},
        ):
            resp = test_client.put(
                "/vendors/cat9000v-uadp",
                json={"memory": 512},
                headers=auth_headers,
            )
        assert resp.status_code == 400
        assert "memory" in resp.json()["detail"].lower()

    def test_update_custom_device_update_returns_none_gives_404(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When update_custom_device returns None it returns 404."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device",
            return_value={"id": "my-device", "name": "My Device"},
        ), patch(
            "app.routers.vendors.update_custom_device", return_value=None
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.put(
                "/vendors/my-device",
                json={"name": "Updated"},
                headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_update_custom_device_requires_auth(self, test_client: TestClient):
        """PUT /vendors/{id} without auth returns 401."""
        resp = test_client.put("/vendors/my-device", json={"name": "X"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /vendors/{device_id}/config — additional cases
# ---------------------------------------------------------------------------


class TestGetDeviceConfigEdgeCases:
    """Edge cases for GET /vendors/{device_id}/config."""

    def test_get_config_custom_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """GET /vendors/{id}/config works for custom devices."""
        custom = {"id": "my-custom", "name": "My Custom", "vendor": "Acme"}
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=custom
        ), patch(
            "app.routers.vendors.get_device_override", return_value={}
        ):
            resp = test_client.get(
                "/vendors/my-custom/config", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base"]["id"] == "my-custom"
        assert data["base"]["isBuiltIn"] is False
        assert data["overrides"] == {}
        assert data["effective"]["id"] == "my-custom"

    def test_get_config_custom_device_with_overrides(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Custom device config merges overrides into effective."""
        custom = {"id": "my-vm", "name": "My VM", "memory": 512}
        overrides = {"memory": 2048}
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=custom
        ), patch(
            "app.routers.vendors.get_device_override", return_value=overrides
        ):
            resp = test_client.get(
                "/vendors/my-vm/config", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["base"]["memory"] == 512
        assert data["overrides"]["memory"] == 2048
        assert data["effective"]["memory"] == 2048

    def test_get_config_requires_auth(self, test_client: TestClient):
        """GET /vendors/{id}/config without auth returns 401."""
        resp = test_client.get("/vendors/linux/config")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /vendors/{device_id}/config — additional cases
# ---------------------------------------------------------------------------


class TestUpdateDeviceConfigEdgeCases:
    """Edge cases for PUT /vendors/{device_id}/config."""

    def test_update_device_config_custom_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """PUT /vendors/{id}/config works for custom devices."""
        custom = {"id": "my-vm", "name": "My VM", "memory": 512, "isBuiltIn": False}
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=custom
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value=None
        ), patch(
            "app.routers.vendors.set_device_override"
        ), patch(
            "app.routers.vendors.get_device_override", return_value={"memory": 1024}
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ):
            resp = test_client.put(
                "/vendors/my-vm/config",
                json={"memory": 1024},
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_update_device_config_device_not_found_returns_404(
        self, test_client: TestClient, auth_headers: dict
    ):
        """PUT /vendors/{id}/config returns 404 when device does not exist."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch("app.routers.vendors.find_custom_device", return_value=None):
            resp = test_client.put(
                "/vendors/ghost-device/config",
                json={"memory": 1024},
                headers=auth_headers,
            )
        assert resp.status_code == 404

    def test_update_device_config_hardware_validation_failure(
        self, test_client: TestClient, auth_headers: dict
    ):
        """PUT /vendors/{id}/config returns 400 on hardware validation failure."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="cat9000v-uadp"
        ):
            resp = test_client.put(
                "/vendors/cat9000v-uadp/config",
                json={"memory": 256},
                headers=auth_headers,
            )
        assert resp.status_code == 400

    def test_update_device_config_requires_auth(self, test_client: TestClient):
        """PUT /vendors/{id}/config without auth returns 401."""
        resp = test_client.put("/vendors/linux/config", json={"memory": 1024})
        assert resp.status_code == 401

    def test_update_device_config_filters_disallowed_fields(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Only allowed override fields are passed through; others are silently dropped."""
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

        captured: dict = {}

        def fake_set_override(device_id: str, payload: dict) -> None:
            captured.update(payload)

        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.set_device_override", side_effect=fake_set_override
        ), patch(
            "app.routers.vendors.get_device_override", return_value={"cpu": 2}
        ), patch(
            "app.routers.vendors.validate_minimum_hardware"
        ), patch(
            "app.routers.vendors._get_vendor_options", return_value={}
        ):
            resp = test_client.put(
                "/vendors/linux/config",
                json={"cpu": 2, "name": "ignored", "isBuiltIn": True},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        # Only allowed field 'cpu' should be in captured payload
        assert "cpu" in captured
        assert "name" not in captured
        assert "isBuiltIn" not in captured


# ---------------------------------------------------------------------------
# DELETE /vendors/{device_id}/config — additional cases
# ---------------------------------------------------------------------------


class TestResetDeviceConfigEdgeCases:
    """Edge cases for DELETE /vendors/{device_id}/config."""

    def test_reset_device_config_no_overrides_returns_message(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Resetting when no overrides exist returns a descriptive message (not 404)."""
        mock_config = MagicMock()
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=mock_config
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value="linux"
        ), patch(
            "app.routers.vendors.delete_device_override", return_value=False
        ):
            resp = test_client.delete(
                "/vendors/linux/config", headers=auth_headers
            )
        assert resp.status_code == 200
        assert "no overrides" in resp.json()["message"].lower()

    def test_reset_device_config_custom_device(
        self, test_client: TestClient, auth_headers: dict
    ):
        """DELETE /vendors/{id}/config works for custom devices."""
        custom = {"id": "my-vm", "name": "My VM"}
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch(
            "app.routers.vendors.find_custom_device", return_value=custom
        ), patch(
            "app.routers.vendors.canonicalize_device_id", return_value=None
        ), patch(
            "app.routers.vendors.delete_device_override", return_value=True
        ):
            resp = test_client.delete(
                "/vendors/my-vm/config", headers=auth_headers
            )
        assert resp.status_code == 200
        assert "reset" in resp.json()["message"].lower()

    def test_reset_device_config_not_found_returns_404(
        self, test_client: TestClient, auth_headers: dict
    ):
        """DELETE /vendors/{id}/config returns 404 when device does not exist."""
        with patch(
            "app.routers.vendors.get_config_by_device", return_value=None
        ), patch("app.routers.vendors.find_custom_device", return_value=None):
            resp = test_client.delete(
                "/vendors/ghost-device/config", headers=auth_headers
            )
        assert resp.status_code == 404

    def test_reset_device_config_requires_auth(self, test_client: TestClient):
        """DELETE /vendors/{id}/config without auth returns 401."""
        resp = test_client.delete("/vendors/linux/config")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /vendors/identity-map — additional cases
# ---------------------------------------------------------------------------


class TestIdentityMapEdgeCases:
    """Edge cases for GET /vendors/identity-map."""

    def test_identity_map_requires_auth(self, test_client: TestClient):
        """GET /vendors/identity-map without auth returns 401."""
        resp = test_client.get("/vendors/identity-map")
        assert resp.status_code == 401

    def test_identity_map_falls_back_when_catalog_not_seeded(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Identity map falls back to registry builder when catalog is not seeded."""

        class FakeConfig:
            kind = "router"
            aliases = ["rtr"]

        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": FakeConfig()})
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {})
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        with patch("app.routers.vendors.catalog_is_seeded", return_value=False):
            resp = test_client.get("/vendors/identity-map", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "canonical_to_runtime_kind" in data
        assert "router" in data["canonical_to_runtime_kind"]

    def test_identity_map_structure(
        self, test_client: TestClient, auth_headers: dict, monkeypatch
    ):
        """Identity map response has all expected top-level keys."""
        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {})
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {})
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        resp = test_client.get("/vendors/identity-map", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "canonical_to_runtime_kind",
            "canonical_to_aliases",
            "alias_to_canonicals",
            "interface_aliases",
        }
        assert expected_keys.issubset(data.keys())
