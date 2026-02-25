from __future__ import annotations

import pytest
from types import SimpleNamespace

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

    monkeypatch.setattr(
        device_service,
        "get_config_by_device",
        lambda device_id: object() if device_id == "builtin" else None,
    )
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

    monkeypatch.setattr(device_service, "get_config_by_device", lambda device_id: object())
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "router")
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

    monkeypatch.setattr(device_service, "get_config_by_device", lambda device_id: object())
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "router")
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 2)

    with pytest.raises(device_service.DeviceHasImagesError):
        service.delete_device("router")


def test_update_device_config_recognizes_built_in_when_key_differs_from_kind(monkeypatch) -> None:
    service = device_service.DeviceService()

    captured: dict[str, str] = {}

    monkeypatch.setattr(
        device_service,
        "get_config_by_device",
        lambda device_id: object() if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)
    monkeypatch.setattr(
        "app.image_store.set_device_override",
        lambda device_id, payload: captured.update({"device_id": device_id}),
    )
    monkeypatch.setattr(
        service,
        "get_device_config",
        lambda device_id: {"base": {"id": device_id}, "overrides": {"cpu": 4}, "effective": {"cpu": 4}},
    )

    result = service.update_device_config("cisco_c8000v", {"cpu": 4})
    assert result["effective"]["cpu"] == 4
    assert captured["device_id"] == "c8000v"


def test_get_device_config_uses_canonical_override_key_for_builtins(monkeypatch) -> None:
    service = device_service.DeviceService()

    fake_config = SimpleNamespace(
        kind="cisco_c8000v",
        vendor="Cisco",
        label="Cisco 8000v",
        device_type=SimpleNamespace(value="router"),
        icon="fa-network-wired",
        versions=["17.16.01a"],
        is_active=True,
        port_naming="GigabitEthernet",
        port_start_index=0,
        max_ports=32,
        memory=8192,
        cpu=2,
        disk_driver="virtio",
        nic_driver="virtio-net-pci",
        machine_type="pc",
        supported_image_kinds=["qcow2"],
        requires_image=True,
        documentation_url=None,
        license_required=False,
        tags=["router"],
        notes=None,
        console_shell=None,
        readiness_probe=None,
        readiness_pattern=None,
        readiness_timeout=300,
    )

    override_lookups: list[str] = []
    monkeypatch.setattr(
        device_service,
        "get_config_by_device",
        lambda device_id: fake_config if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr(
        "app.image_store.get_device_override",
        lambda device_id: override_lookups.append(device_id) or {"cpu": 4},
    )
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)
    monkeypatch.setattr("agent.vendors._get_vendor_options", lambda config: {})

    result = service.get_device_config("cisco_c8000v")

    assert result["base"]["id"] == "c8000v"
    assert result["overrides"]["cpu"] == 4
    assert override_lookups == ["c8000v"]


def test_reset_device_config_uses_canonical_id_for_builtins(monkeypatch) -> None:
    service = device_service.DeviceService()

    calls: dict[str, str] = {}
    def fake_delete_override(device_id: str) -> bool:
        calls["device_id"] = device_id
        return True

    monkeypatch.setattr(
        device_service,
        "get_config_by_device",
        lambda device_id: object() if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr("app.image_store.delete_device_override", fake_delete_override)
    monkeypatch.setattr("app.image_store.find_custom_device", lambda device_id: None)

    result = service.reset_device_config("cisco_c8000v")

    assert calls["device_id"] == "c8000v"
    assert result["message"] == "Device 'c8000v' reset to defaults"


def test_hide_restore_device_uses_canonical_device_id(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr(
        device_service,
        "get_config_by_device",
        lambda device_id: object() if device_id in {"c8000v", "cisco_c8000v"} else None,
    )
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda device_id: "c8000v")
    monkeypatch.setattr("app.image_store.get_device_image_count", lambda device_id: 0)

    hidden_state = {"value": False}
    calls: dict[str, str] = {}

    def fake_is_hidden(device_id: str) -> bool:
        return hidden_state["value"] if device_id == "c8000v" else False

    def fake_hide(device_id: str):
        calls["hide"] = device_id
        hidden_state["value"] = True

    def fake_unhide(device_id: str):
        calls["unhide"] = device_id
        hidden_state["value"] = False

    monkeypatch.setattr("app.image_store.is_device_hidden", fake_is_hidden)
    monkeypatch.setattr("app.image_store.hide_device", fake_hide)
    monkeypatch.setattr("app.image_store.unhide_device", fake_unhide)

    hide_result = service.hide_device("cisco_c8000v")
    assert "c8000v" in hide_result["message"]
    assert calls["hide"] == "c8000v"

    restore_result = service.restore_device("cisco_c8000v")
    assert "c8000v" in restore_result["message"]
    assert calls["unhide"] == "c8000v"


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


def test_get_image_runtime_metadata_accepts_image_id(monkeypatch) -> None:
    manifest = {
        "images": [
            {
                "id": "qcow2:nxosv9000.qcow2",
                "reference": "/var/lib/archetype/images/nxosv9000.qcow2",
                "memory_mb": 12288,
                "cpu_count": 4,
                "disk_driver": "sata",
                "nic_driver": "e1000",
                "efi_boot": True,
                "efi_vars": "stateless",
            }
        ]
    }
    monkeypatch.setattr("app.image_store.load_manifest", lambda: manifest)

    meta = device_service.get_image_runtime_metadata("qcow2:nxosv9000.qcow2")
    assert meta["memory"] == 12288
    assert meta["cpu"] == 4
    assert meta["disk_driver"] == "sata"
    assert meta["nic_driver"] == "e1000"
    assert meta["efi_boot"] is True
    assert meta["efi_vars"] == "stateless"


def test_resolve_hardware_specs_uses_versioned_manifest_lookup(monkeypatch) -> None:
    service = device_service.DeviceService()

    monkeypatch.setattr(device_service, "_get_config_by_kind", lambda device_id: None)
    monkeypatch.setattr(device_service, "canonicalize_device_id", lambda _device_id: "nxosv9000")
    monkeypatch.setattr(device_service, "find_custom_device", lambda device_id: None)
    monkeypatch.setattr(device_service, "get_device_override", lambda device_id: {})

    lookups = []

    def _fake_find_image_reference(device_id: str, version: str | None = None) -> str | None:
        lookups.append((device_id, version))
        if device_id == "nxosv9000" and version == "10.5.3.F":
            return "/var/lib/archetype/images/nxosv9000.qcow2"
        return None

    monkeypatch.setattr(device_service, "find_image_reference", _fake_find_image_reference)
    monkeypatch.setattr(
        device_service,
        "get_image_runtime_metadata",
        lambda image_reference: {
            "efi_boot": True,
            "efi_vars": "stateless",
            "machine_type": "pc-q35-6.2",
        } if image_reference == "/var/lib/archetype/images/nxosv9000.qcow2" else {},
    )

    specs = service.resolve_hardware_specs("cisco_n9kv", version="10.5.3.F")

    assert lookups == [("cisco_n9kv", "10.5.3.F"), ("nxosv9000", "10.5.3.F")]
    assert specs["efi_boot"] is True
    assert specs["efi_vars"] == "stateless"
    assert specs["machine_type"] == "pc-q35-6.2"
