from __future__ import annotations

import app.routers.vendors as vendors_router  # noqa: F401



def test_list_vendors_filters_hidden(test_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.vendors.get_vendors_for_ui",
        lambda: [{"name": "Compute", "models": [{"id": "linux"}, {"id": "hidden"}]}],
    )
    monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: ["hidden"])
    monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

    resp = test_client.get("/vendors")
    assert resp.status_code == 200
    models = resp.json()[0]["models"]
    assert {m["id"] for m in models} == {"linux"}


def test_list_vendors_includes_server_driven_compatibility_aliases(test_client, monkeypatch) -> None:
    class FakeConfig:
        kind = "cisco_cat9000v_uadp"
        aliases = ["cat9000v_uadp_legacy"]

    monkeypatch.setattr(
        "agent.vendors.get_vendors_for_ui",
        lambda: [{"name": "Network", "models": [{"id": "cat9000v-uadp", "name": "Cat9k"}]}],
    )
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"cat9000v-uadp": FakeConfig()})
    monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
    monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])
    monkeypatch.setattr(
        "app.image_store.get_image_compatibility_aliases",
        lambda: {"cat9000v-uadp": ["cisco_cat9kv"]},
    )

    resp = test_client.get("/vendors")
    assert resp.status_code == 200
    model = resp.json()[0]["models"][0]
    assert model["id"] == "cat9000v-uadp"
    assert set(model["compatibilityAliases"]) == {
        "cisco_cat9kv",
        "cisco_cat9000v_uadp",
        "cat9000v_uadp_legacy",
    }


def test_identity_map_endpoint_uses_registry_when_catalog_not_seeded(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    class FakeConfig:
        kind = "ceos"
        aliases = ["eos", "arista_eos"]

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"ceos": FakeConfig()})
    monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {"ceos": ["arista_ceos"]})
    monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

    resp = test_client.get("/vendors/identity-map", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["canonical_to_runtime_kind"]["ceos"] == "ceos"
    assert set(payload["canonical_to_aliases"]["ceos"]) == {"eos", "arista_eos", "arista_ceos"}
    assert payload["interface_aliases"]["eos"] == "ceos"


def test_add_custom_device_errors(test_client, auth_headers, monkeypatch) -> None:
    class FakeConfig:
        kind = "builtin"

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id == "builtin" else None,
    )
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)

    resp = test_client.post("/vendors", json={"name": "X"}, headers=auth_headers)
    assert resp.status_code == 400

    resp = test_client.post("/vendors", json={"id": "builtin", "name": "X"}, headers=auth_headers)
    assert resp.status_code == 409


def test_delete_device_with_images(test_client, auth_headers, monkeypatch) -> None:
    cfg = object()
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": cfg})
    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: cfg if device_id == "router" else None,
    )
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 1)

    resp = test_client.delete("/vendors/router", headers=auth_headers)
    assert resp.status_code == 400


def test_restore_hidden_device(test_client, auth_headers, monkeypatch) -> None:
    cfg = object()
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": cfg})
    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: cfg if device_id == "router" else None,
    )
    monkeypatch.setattr("app.image_store.is_device_hidden", lambda device_id: True)

    called = {}

    def fake_unhide(device_id: str):
        called["id"] = device_id

    monkeypatch.setattr("app.image_store.unhide_device", fake_unhide)

    resp = test_client.post("/vendors/router/restore", headers=auth_headers)
    assert resp.status_code == 200
    assert called["id"] == "router"


def test_get_device_config_built_in(test_client, auth_headers, monkeypatch) -> None:
    class FakeConfig:
        kind = "router"
        vendor = "Vendor"
        label = "Vendor Router"
        device_type = type("DT", (), {"value": "router"})
        icon = "fa-router"
        versions = ["1.0"]
        is_active = True
        port_naming = "eth"
        port_start_index = 0
        max_ports = 8
        memory = 1024
        cpu = 1
        requires_image = True
        supported_image_kinds = ["docker"]
        documentation_url = None
        license_required = False
        tags = []
        notes = None
        console_shell = None
        readiness_probe = None
        readiness_pattern = None
        readiness_timeout = None

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id in {"router", "router-alias"} else None,
    )
    monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda device_id: "router")
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {"opt": True})
    monkeypatch.setattr(
        "app.image_store.get_device_override",
        lambda device_id: {"memory": 2048} if device_id == "router" else {},
    )

    resp = test_client.get("/vendors/router-alias/config", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["base"]["id"] == "router"
    assert data["effective"]["memory"] == 2048


def test_update_device_config_built_in(test_client, auth_headers, monkeypatch) -> None:
    class FakeConfig:
        kind = "router"
        vendor = "Vendor"
        label = "Vendor Router"
        device_type = type("DT", (), {"value": "router"})
        icon = "fa-router"
        versions = ["1.0"]
        is_active = True
        port_naming = "eth"
        port_start_index = 0
        max_ports = 8
        memory = 1024
        cpu = 1
        requires_image = True
        supported_image_kinds = ["docker"]
        documentation_url = None
        license_required = False
        tags = []
        notes = None
        console_shell = None
        readiness_probe = None
        readiness_pattern = None
        readiness_timeout = None

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id in {"router", "router-alias"} else None,
    )
    monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda device_id: "router")
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {})
    monkeypatch.setattr("app.image_store.get_device_override", lambda device_id: {"cpu": 4})
    called: dict[str, str] = {}

    def fake_set_override(device_id: str, payload: dict):
        called["id"] = device_id

    monkeypatch.setattr("app.image_store.set_device_override", fake_set_override)
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: {"id": device_id})

    resp = test_client.put("/vendors/router-alias/config", json={"cpu": 4}, headers=auth_headers)
    assert resp.status_code == 200
    assert called["id"] == "router"
    assert resp.json()["base"]["id"] == "router"
    assert resp.json()["effective"]["cpu"] == 4


def test_add_custom_device_conflict_when_key_differs_from_kind(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    class FakeConfig:
        kind = "cisco_c8000v"

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)

    resp = test_client.post("/vendors", json={"id": "c8000v", "name": "X"}, headers=auth_headers)
    assert resp.status_code == 409


def test_delete_device_resolves_alias_to_canonical_hidden_id(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    cfg = object()
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"c8000v": cfg})
    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: cfg if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 0)
    monkeypatch.setattr("app.image_store.is_device_hidden", lambda device_id: False)

    called = {}

    def fake_hide(device_id: str):
        called["id"] = device_id

    monkeypatch.setattr("app.image_store.hide_device", fake_hide)

    resp = test_client.delete("/vendors/cisco_c8000v", headers=auth_headers)
    assert resp.status_code == 200
    assert called["id"] == "c8000v"
    assert "c8000v" in resp.json()["message"]


def test_restore_device_resolves_alias_to_canonical_hidden_id(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    cfg = object()
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"c8000v": cfg})
    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: cfg if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr("app.image_store.is_device_hidden", lambda device_id: device_id == "c8000v")

    called = {}

    def fake_unhide(device_id: str):
        called["id"] = device_id

    monkeypatch.setattr("app.image_store.unhide_device", fake_unhide)

    resp = test_client.post("/vendors/cisco_c8000v/restore", headers=auth_headers)
    assert resp.status_code == 200
    assert called["id"] == "c8000v"
    assert "c8000v" in resp.json()["message"]


def test_update_device_config_recognizes_built_in_when_key_differs_from_kind(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    class FakeConfig:
        kind = "cisco_c8000v"
        vendor = "Cisco"
        label = "Catalyst SD-WAN Edge"
        device_type = type("DT", (), {"value": "router"})
        icon = "fa-router"
        versions = ["17.16.01a"]
        is_active = True
        port_naming = "GigabitEthernet"
        port_start_index = 1
        max_ports = 8
        memory = 8192
        cpu = 2
        requires_image = True
        supported_image_kinds = ["qcow2"]
        documentation_url = None
        license_required = True
        tags = []
        notes = None
        console_shell = None
        readiness_probe = None
        readiness_pattern = None
        readiness_timeout = None

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id == "c8000v" else None,
    )
    monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {})
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)
    monkeypatch.setattr("app.image_store.set_device_override", lambda device_id, payload: None)
    monkeypatch.setattr("app.image_store.get_device_override", lambda device_id: {"cpu": 4})

    resp = test_client.put("/vendors/c8000v/config", json={"cpu": 4}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["effective"]["cpu"] == 4


def test_reset_device_config_recognizes_built_in_when_key_differs_from_kind(
    test_client,
    auth_headers,
    monkeypatch,
) -> None:
    class FakeConfig:
        kind = "cisco_c8000v"

    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: FakeConfig() if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)
    called: dict[str, str] = {}

    def fake_delete_override(device_id: str) -> bool:
        called["id"] = device_id
        return True

    monkeypatch.setattr("app.image_store.delete_device_override", fake_delete_override)

    resp = test_client.delete("/vendors/cisco_c8000v/config", headers=auth_headers)
    assert resp.status_code == 200
    assert called["id"] == "c8000v"
    assert "c8000v" in resp.json()["message"].lower()
    assert "reset to defaults" in resp.json()["message"].lower()


def test_update_custom_device_rejects_built_in_alias(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.vendors.get_config_by_device",
        lambda device_id: object() if device_id in {"eos", "ceos"} else None,
    )

    resp = test_client.put("/vendors/eos", json={"name": "X"}, headers=auth_headers)
    assert resp.status_code == 400
    assert "cannot modify built-in vendor devices" in resp.json()["detail"].lower()
