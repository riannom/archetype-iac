from __future__ import annotations

import json
from uuid import uuid4

from app import models
from app.image_store import save_manifest


def _seed_vendor_device(
    test_db,
    *,
    canonical_device_id: str,
    runtime_kind: str | None = None,
) -> models.CatalogDeviceType:
    vendor = models.CatalogVendor(
        id=str(uuid4()),
        vendor_key=f"vendor_{canonical_device_id.replace('-', '_')}",
        display_name="Test Vendor",
        lifecycle_status="active",
        metadata_json="{}",
    )
    device = models.CatalogDeviceType(
        id=str(uuid4()),
        canonical_device_id=canonical_device_id,
        vendor_id=vendor.id,
        runtime_kind=runtime_kind or canonical_device_id,
        display_name=canonical_device_id,
        device_class="router",
        source="builtin",
        lifecycle_status="active",
        metadata_json="{}",
    )
    revision = models.CatalogDeviceRevision(
        id=str(uuid4()),
        device_type_id=device.id,
        version_tag="current",
        runtime_kind=device.runtime_kind,
        supported_image_kinds_json='["qcow2","docker"]',
        metadata_json="{}",
        is_current=True,
    )
    test_db.add_all([vendor, device, revision])
    test_db.flush()
    return device


def _seed_shared_cat9k_catalog(test_db) -> None:
    dev_uadp = _seed_vendor_device(
        test_db,
        canonical_device_id="cat9000v-uadp",
        runtime_kind="cisco_cat9000v_uadp",
    )
    dev_q200 = _seed_vendor_device(
        test_db,
        canonical_device_id="cat9000v-q200",
        runtime_kind="cisco_cat9000v_q200",
    )
    alias_rows = [
        models.CatalogDeviceAlias(
            id=str(uuid4()),
            device_type_id=dev_uadp.id,
            alias="cisco_cat9kv",
            alias_type="compatibility",
            source="test",
            is_active=True,
        ),
        models.CatalogDeviceAlias(
            id=str(uuid4()),
            device_type_id=dev_q200.id,
            alias="cisco_cat9kv",
            alias_type="compatibility",
            source="test",
            is_active=True,
        ),
    ]
    image = models.CatalogImage(
        id=str(uuid4()),
        external_id="qcow2:cat9k-shared",
        kind="qcow2",
        reference="/tmp/cat9k-shared.qcow2",
        filename="cat9k-shared.qcow2",
        source="manifest",
        metadata_json=json.dumps(
            {
                "id": "qcow2:cat9k-shared",
                "kind": "qcow2",
                "reference": "/tmp/cat9k-shared.qcow2",
                "filename": "cat9k-shared.qcow2",
                "device_id": "cat9000v-uadp",
                "compatible_devices": ["cat9000v-uadp", "cat9000v-q200"],
                "default_for_devices": ["cat9000v-uadp", "cat9000v-q200"],
                "is_default": True,
            }
        ),
    )
    compat_rows = [
        models.CatalogImageCompatibility(
            id=str(uuid4()),
            image_id=image.id,
            device_type_id=dev_uadp.id,
            source="test",
        ),
        models.CatalogImageCompatibility(
            id=str(uuid4()),
            image_id=image.id,
            device_type_id=dev_q200.id,
            source="test",
        ),
    ]
    default_rows = [
        models.CatalogImageDefault(
            id=str(uuid4()),
            device_type_id=dev_uadp.id,
            image_id=image.id,
            source="test",
        ),
        models.CatalogImageDefault(
            id=str(uuid4()),
            device_type_id=dev_q200.id,
            image_id=image.id,
            source="test",
        ),
    ]
    test_db.add_all(alias_rows + [image] + compat_rows + default_rows)
    test_db.commit()


def test_catalog_db_library_reads_and_alias_matching(
    test_client,
    test_db,
    admin_auth_headers,
) -> None:
    _seed_shared_cat9k_catalog(test_db)

    resp = test_client.get("/images/library", headers=admin_auth_headers)
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert len(images) == 1
    image = images[0]
    assert image["id"] == "qcow2:cat9k-shared"
    assert set(image["compatible_devices"]) == {"cat9000v-uadp", "cat9000v-q200"}
    assert set(image["default_for_devices"]) == {"cat9000v-uadp", "cat9000v-q200"}

    q200 = test_client.get("/images/devices/cat9000v-q200/images", headers=admin_auth_headers)
    assert q200.status_code == 200
    assert [img["id"] for img in q200.json()["images"]] == ["qcow2:cat9k-shared"]

    shared_alias = test_client.get("/images/devices/cisco_cat9kv/images", headers=admin_auth_headers)
    assert shared_alias.status_code == 200
    assert [img["id"] for img in shared_alias.json()["images"]] == ["qcow2:cat9k-shared"]


def test_catalog_db_unassign_updates_compat_defaults_and_logs_event(
    test_client,
    test_db,
    admin_auth_headers,
) -> None:
    _seed_shared_cat9k_catalog(test_db)

    resp = test_client.post(
        "/images/library/qcow2:cat9k-shared/unassign",
        json={"device_id": "cat9000v-uadp"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    image = resp.json()["image"]
    assert image["device_id"] == "cat9000v-q200"
    assert image["compatible_devices"] == ["cat9000v-q200"]
    assert image["default_for_devices"] == ["cat9000v-q200"]
    assert image["is_default"] is True

    compat = (
        test_db.query(models.CatalogImageCompatibility, models.CatalogDeviceType.canonical_device_id)
        .join(
            models.CatalogDeviceType,
            models.CatalogDeviceType.id == models.CatalogImageCompatibility.device_type_id,
        )
        .all()
    )
    assert {canonical for _, canonical in compat} == {"cat9000v-q200"}

    defaults = (
        test_db.query(models.CatalogImageDefault, models.CatalogDeviceType.canonical_device_id)
        .join(
            models.CatalogDeviceType,
            models.CatalogDeviceType.id == models.CatalogImageDefault.device_type_id,
        )
        .all()
    )
    assert {canonical for _, canonical in defaults} == {"cat9000v-q200"}

    event = (
        test_db.query(models.CatalogIngestEvent)
        .filter(models.CatalogIngestEvent.event_type == "image_unassign")
        .first()
    )
    assert event is not None


def test_vendors_compatibility_aliases_are_db_driven(
    test_client,
    test_db,
    monkeypatch,
) -> None:
    _seed_shared_cat9k_catalog(test_db)

    monkeypatch.setattr(
        "agent.vendors.get_vendors_for_ui",
        lambda: [{"name": "Network", "models": [{"id": "cat9000v-uadp", "name": "Cat9k UADP"}]}],
    )
    monkeypatch.setattr("agent.vendors.get_config_by_device", lambda _device_id: None)
    monkeypatch.setattr("app.image_store.load_hidden_devices", lambda: [])
    monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

    resp = test_client.get("/vendors")
    assert resp.status_code == 200
    model = resp.json()[0]["models"][0]
    assert model["id"] == "cat9000v-uadp"
    assert "cisco_cat9kv" in model["compatibilityAliases"]
    assert "cisco_cat9000v_uadp" in model["compatibilityAliases"]


def test_manifest_write_syncs_catalog_when_seeded(
    test_client,
    test_db,
) -> None:
    _seed_vendor_device(test_db, canonical_device_id="ceos", runtime_kind="ceos")
    test_db.commit()

    manifest = {
        "images": [
            {
                "id": "docker:ceos:4.29.0F",
                "kind": "docker",
                "reference": "ceos:4.29.0F",
                "device_id": "ceos",
                "compatible_devices": ["ceos"],
                "default_for_devices": ["ceos"],
                "is_default": True,
            }
        ]
    }
    save_manifest(manifest)

    image = (
        test_db.query(models.CatalogImage)
        .filter(models.CatalogImage.external_id == "docker:ceos:4.29.0F")
        .first()
    )
    assert image is not None

    compat = (
        test_db.query(models.CatalogImageCompatibility, models.CatalogDeviceType.canonical_device_id)
        .join(
            models.CatalogDeviceType,
            models.CatalogDeviceType.id == models.CatalogImageCompatibility.device_type_id,
        )
        .all()
    )
    assert {canonical for _, canonical in compat} == {"ceos"}


def test_manifest_default_scope_is_added_to_compatibility(
    test_client,
    test_db,
    admin_auth_headers,
) -> None:
    _seed_vendor_device(test_db, canonical_device_id="ceos", runtime_kind="ceos")
    test_db.commit()

    save_manifest(
        {
            "images": [
                {
                    "id": "docker:ceos:4.30.0F",
                    "kind": "docker",
                    "reference": "ceos:4.30.0F",
                    "device_id": None,
                    "compatible_devices": [],
                    "default_for_devices": ["ceos"],
                    "is_default": True,
                }
            ]
        }
    )

    resp = test_client.get("/images/library", headers=admin_auth_headers)
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert len(images) == 1
    assert images[0]["default_for_devices"] == ["ceos"]
    assert images[0]["compatible_devices"] == ["ceos"]


def test_vendors_identity_map_reports_ambiguous_aliases(
    test_client,
    test_db,
    admin_auth_headers,
) -> None:
    _seed_shared_cat9k_catalog(test_db)

    resp = test_client.get("/vendors/identity-map", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert sorted(data["alias_to_canonicals"]["cisco_cat9kv"]) == [
        "cat9000v-q200",
        "cat9000v-uadp",
    ]
    assert "cisco_cat9kv" not in data["interface_aliases"]
