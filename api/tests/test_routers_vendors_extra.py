from __future__ import annotations

import app.routers.vendors as vendors_router  # noqa: F401

import pytest


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


def test_add_custom_device_errors(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"builtin": object()})
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)

    resp = test_client.post("/vendors", json={"name": "X"}, headers=auth_headers)
    assert resp.status_code == 400

    resp = test_client.post("/vendors", json={"id": "builtin", "name": "X"}, headers=auth_headers)
    assert resp.status_code == 409


def test_delete_device_with_images(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 1)

    resp = test_client.delete("/vendors/router", headers=auth_headers)
    assert resp.status_code == 400


def test_restore_hidden_device(test_client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
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

    monkeypatch.setattr("agent.vendors._get_config_by_kind", lambda device_id: FakeConfig())
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {"opt": True})
    monkeypatch.setattr("app.image_store.get_device_override", lambda device_id: {"memory": 2048})

    resp = test_client.get("/vendors/router/config", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
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

    monkeypatch.setattr("agent.vendors._get_config_by_kind", lambda device_id: FakeConfig())
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {})
    monkeypatch.setattr("app.image_store.get_device_override", lambda device_id: {"cpu": 4})
    monkeypatch.setattr("app.image_store.set_device_override", lambda device_id, payload: None)
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: {"id": device_id})

    resp = test_client.put("/vendors/router/config", json={"cpu": 4}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["effective"]["cpu"] == 4
