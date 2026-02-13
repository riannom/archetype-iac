from __future__ import annotations

import pytest

import app.services.device_service as device_service


def test_list_vendors_filters_hidden_and_merges_custom(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr(
        "agent.vendors.get_vendors_for_ui",
        lambda: [
            {
                "name": "Compute",
                "models": [
                    {"id": "linux", "name": "Linux"},
                    {"id": "hidden", "name": "Hidden"},
                ],
            }
        ],
    )
    monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: ["hidden"])
    monkeypatch.setattr(
        "app.image_store.load_custom_devices",
        lambda: [{"id": "custom1", "name": "Custom", "category": "Compute"}],
    )

    result = service.list_vendors()
    models = result[0]["models"]

    assert {m["id"] for m in models} == {"linux", "custom1"}


def test_add_custom_device_validation(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"builtin": object()})
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)
    monkeypatch.setattr("app.image_store.add_custom_device", lambda payload: payload)

    with pytest.raises(device_service.DeviceValidationError):
        service.add_custom_device({"id": ""})

    with pytest.raises(device_service.DeviceValidationError):
        service.add_custom_device({"id": "custom", "name": ""})

    with pytest.raises(device_service.DeviceConflictError):
        service.add_custom_device({"id": "builtin", "name": "Builtin"})

    created = service.add_custom_device({"id": "custom", "name": "Custom"})
    assert created["id"] == "custom"


def test_delete_device_handles_built_in(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 0)
    monkeypatch.setattr("app.image_store.is_device_hidden", lambda device_id: False)

    hidden = {}

    def fake_hide(device_id: str):
        hidden["id"] = device_id

    monkeypatch.setattr("app.image_store.hide_device", fake_hide)

    result = service.delete_device("router")
    assert "hidden successfully" in result["message"]
    assert hidden["id"] == "router"


def test_delete_device_blocks_with_images(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"router": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 2)

    with pytest.raises(device_service.DeviceHasImagesError):
        service.delete_device("router")


def test_update_device_config_custom(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: {"id": device_id})
    monkeypatch.setattr("app.image_store.set_device_override", lambda device_id, payload: None)
    monkeypatch.setattr(
        "app.image_store.get_device_override",
        lambda device_id: {"cpu": 2},
    )

    def fake_get_device_config(device_id: str):
        return {"base": {"id": device_id}, "overrides": {"cpu": 2}, "effective": {"cpu": 2}}

    monkeypatch.setattr(service, "get_device_config", fake_get_device_config)

    result = service.update_device_config("custom", {"cpu": 2, "invalid": 1})
    assert result["effective"]["cpu"] == 2


def test_update_device_config_rejects_underprovisioned_cat9k(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {"cat9000v-uadp": object()})
    monkeypatch.setattr("agent.vendors.get_kind_for_device", lambda device_id: device_id)

    with pytest.raises(device_service.DeviceValidationError, match="memory intensive"):
        service.update_device_config("cat9000v-uadp", {"memory": 12288, "cpu": 2})


def test_resolve_hardware_specs_rejects_underprovisioned_cat9k(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr(device_service, "_get_config_by_kind", lambda device_id: None)
    monkeypatch.setattr(device_service, "get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr(
        device_service,
        "find_custom_device",
        lambda device_id: {"id": device_id, "memory": 12288, "cpu": 2},
    )
    monkeypatch.setattr(device_service, "get_device_override", lambda device_id: {})

    with pytest.raises(device_service.DeviceValidationError, match="memory intensive"):
        service.resolve_hardware_specs("cat9000v-uadp")


def test_resolve_hardware_specs_includes_efi_fields(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr(device_service, "_get_config_by_kind", lambda device_id: None)
    monkeypatch.setattr(device_service, "get_kind_for_device", lambda device_id: device_id)
    monkeypatch.setattr(device_service, "find_custom_device", lambda device_id: None)
    monkeypatch.setattr(device_service, "get_device_override", lambda device_id: {})
    monkeypatch.setattr(
        device_service,
        "get_image_runtime_metadata",
        lambda image_reference: {
            "libvirt_driver": "qemu",
            "efi_boot": True,
            "efi_vars": "stateless",
            "cpu_limit": 75,
            "max_ports": 65,
            "port_naming": "Ethernet1/",
        },
    )

    specs = service.resolve_hardware_specs("nxosv9000", None, "/tmp/nxosv.qcow2")
    assert specs["libvirt_driver"] == "qemu"
    assert specs["efi_boot"] is True
    assert specs["efi_vars"] == "stateless"
    assert specs["cpu_limit"] == 75
    assert specs["max_ports"] == 65
    assert specs["port_naming"] == "Ethernet1/"
