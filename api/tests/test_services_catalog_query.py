"""Tests for app.services.catalog_query — index build, cache, query, persistence."""
from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import pytest

from app.services.catalog_service import (
    AliasIndex,
    CatalogImageNotFoundError,
    ImageIndexCache,
    invalidate_image_index_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_alias_index() -> AliasIndex:
    return AliasIndex(
        canonical_by_type_id={},
        type_id_by_canonical={},
        canonical_to_aliases={},
        alias_to_canonicals={},
    )


def _simple_alias_index(canonicals: dict[str, str] | None = None, aliases: dict[str, set[str]] | None = None) -> AliasIndex:
    canonicals = canonicals or {"type-1": "ceos", "type-2": "srlinux"}
    type_id_by_canonical = {v: k for k, v in canonicals.items()}
    return AliasIndex(
        canonical_by_type_id=canonicals,
        type_id_by_canonical=type_id_by_canonical,
        canonical_to_aliases=aliases or {},
        alias_to_canonicals={},
    )


def _fake_image_row(external_id: str, kind: str = "docker", metadata_json: str = "{}",
                     reference: str | None = None, filename: str | None = None,
                     vendor_name: str | None = None, version: str | None = None,
                     source: str = "api", digest_sha256: str | None = None,
                     size_bytes: int | None = None, imported_at=None):
    row = SimpleNamespace(
        id=f"db-{external_id}",
        external_id=external_id,
        kind=kind,
        metadata_json=metadata_json,
        reference=reference,
        filename=filename,
        vendor_name=vendor_name,
        version=version,
        source=source,
        digest_sha256=digest_sha256,
        size_bytes=size_bytes,
        imported_at=imported_at,
    )
    return row


def _make_index_cache(
    images: list[dict] | None = None,
    image_ids_by_canonical: dict | None = None,
) -> ImageIndexCache:
    images = images or []
    ordered_ids = [img["id"] for img in images]
    images_by_external_id = {img["id"]: img for img in images}
    return ImageIndexCache(
        stamp=(len(images), None, 0, None, 0, None, 0, None, 0, None),
        ordered_images=images,
        ordered_ids=ordered_ids,
        images_by_external_id=images_by_external_id,
        image_ids_by_canonical=image_ids_by_canonical or defaultdict(set),
        alias_index=_empty_alias_index(),
    )


# ---------------------------------------------------------------------------
# Tests: _build_index_stamp
# ---------------------------------------------------------------------------

class TestBuildIndexStamp:
    def test_stamp_returns_tuple_of_ten_elements(self, test_db) -> None:
        from app.services.catalog_query import _build_index_stamp
        stamp = _build_index_stamp(test_db)
        assert isinstance(stamp, tuple)
        assert len(stamp) == 10

    def test_stamp_changes_when_image_inserted(self, test_db) -> None:
        from app.services.catalog_query import _build_index_stamp
        from app import models
        from uuid import uuid4

        stamp_before = _build_index_stamp(test_db)

        vendor = models.CatalogVendor(
            id=str(uuid4()), vendor_key="cisco", display_name="Cisco",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(vendor)
        test_db.flush()

        img = models.CatalogImage(
            id=str(uuid4()), external_id="docker:ceos:4.28",
            kind="docker", source="api", metadata_json="{}",
        )
        test_db.add(img)
        test_db.flush()

        stamp_after = _build_index_stamp(test_db)
        assert stamp_before != stamp_after

    def test_stamp_reflects_alias_changes(self, test_db) -> None:
        from app.services.catalog_query import _build_index_stamp
        from app import models
        from uuid import uuid4

        stamp_before = _build_index_stamp(test_db)

        vendor = models.CatalogVendor(
            id=str(uuid4()), vendor_key="cisco", display_name="Cisco",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(vendor)
        test_db.flush()

        device = models.CatalogDeviceType(
            id=str(uuid4()), canonical_device_id="ceos",
            vendor_id=vendor.id, runtime_kind="ceos",
            display_name="cEOS", source="manual",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(device)
        test_db.flush()

        alias = models.CatalogDeviceAlias(
            id=str(uuid4()), device_type_id=device.id,
            alias="arista_ceos", alias_type="compatibility",
        )
        test_db.add(alias)
        test_db.flush()

        stamp_after = _build_index_stamp(test_db)
        assert stamp_before != stamp_after


# ---------------------------------------------------------------------------
# Tests: _get_image_index (caching)
# ---------------------------------------------------------------------------

class TestGetImageIndex:
    def test_returns_cache_when_stamp_matches(self, test_db) -> None:
        from app.services.catalog_query import _get_image_index
        idx1 = _get_image_index(test_db)
        idx2 = _get_image_index(test_db)
        # Same stamp, same object
        assert idx1 is idx2

    def test_force_refresh_rebuilds_cache(self, test_db) -> None:
        from app.services.catalog_query import _get_image_index
        idx1 = _get_image_index(test_db)
        idx2 = _get_image_index(test_db, force_refresh=True)
        # Force refresh builds a new object even if stamp matches
        assert idx1 is not idx2

    def test_invalidation_clears_specific_bind(self, test_db) -> None:
        from app.services.catalog_query import _get_image_index
        idx1 = _get_image_index(test_db)
        invalidate_image_index_cache(test_db)
        # Next call must rebuild
        idx2 = _get_image_index(test_db)
        assert idx1 is not idx2

    def test_invalidation_without_session_clears_all(self, test_db) -> None:
        from app.services.catalog_query import _get_image_index
        _get_image_index(test_db)
        invalidate_image_index_cache(None)
        idx2 = _get_image_index(test_db)
        assert idx2 is not None


# ---------------------------------------------------------------------------
# Tests: list_catalog_library_images / get_catalog_library_image
# ---------------------------------------------------------------------------

class TestListAndGetCatalogImages:
    def test_list_empty_catalog_returns_empty(self, test_db) -> None:
        from app.services.catalog_query import list_catalog_library_images
        result = list_catalog_library_images(test_db)
        assert result == []

    def test_get_nonexistent_image_returns_none(self, test_db) -> None:
        from app.services.catalog_query import get_catalog_library_image
        result = get_catalog_library_image(test_db, "nonexistent")
        assert result is None

    def test_list_returns_images_sorted_by_external_id(self, test_db) -> None:
        from app.services.catalog_query import list_catalog_library_images
        from app import models
        from uuid import uuid4

        for ext_id in ["z-image", "a-image", "m-image"]:
            test_db.add(models.CatalogImage(
                id=str(uuid4()), external_id=ext_id,
                kind="docker", source="api", metadata_json="{}",
            ))
        test_db.flush()

        result = list_catalog_library_images(test_db, force_refresh=True)
        ids = [img["id"] for img in result]
        assert ids == sorted(ids)

    def test_get_returns_projected_image(self, test_db) -> None:
        from app.services.catalog_query import get_catalog_library_image
        from app import models
        from uuid import uuid4

        test_db.add(models.CatalogImage(
            id=str(uuid4()), external_id="my-image",
            kind="docker", source="api", metadata_json='{"foo":"bar"}',
            reference="ceos:4.28", filename="ceos.tar",
        ))
        test_db.flush()

        result = get_catalog_library_image(test_db, "my-image", force_refresh=True)
        assert result is not None
        assert result["id"] == "my-image"
        assert result["kind"] == "docker"
        assert result["reference"] == "ceos:4.28"
        assert result["filename"] == "ceos.tar"


# ---------------------------------------------------------------------------
# Tests: list_catalog_images_for_device / count
# ---------------------------------------------------------------------------

class TestImagesForDevice:
    def test_unknown_device_returns_empty(self, test_db) -> None:
        from app.services.catalog_query import list_catalog_images_for_device
        result = list_catalog_images_for_device(test_db, "unknown_device")
        assert result == []

    def test_count_returns_zero_for_unknown(self, test_db) -> None:
        from app.services.catalog_query import count_catalog_images_for_device
        assert count_catalog_images_for_device(test_db, "unknown") == 0

    def test_images_returned_for_compatible_device(self, test_db) -> None:
        from app.services.catalog_query import list_catalog_images_for_device
        from app import models
        from uuid import uuid4

        vendor = models.CatalogVendor(
            id=str(uuid4()), vendor_key="arista", display_name="Arista",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(vendor)
        test_db.flush()

        device = models.CatalogDeviceType(
            id=str(uuid4()), canonical_device_id="ceos",
            vendor_id=vendor.id, runtime_kind="ceos",
            display_name="cEOS", source="manual",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(device)
        test_db.flush()

        img = models.CatalogImage(
            id=str(uuid4()), external_id="docker:ceos:4.28",
            kind="docker", source="api", metadata_json='{"device_id":"ceos"}',
        )
        test_db.add(img)
        test_db.flush()

        compat = models.CatalogImageCompatibility(
            id=str(uuid4()), image_id=img.id,
            device_type_id=device.id, source="api",
        )
        test_db.add(compat)
        test_db.flush()

        invalidate_image_index_cache(test_db)
        result = list_catalog_images_for_device(test_db, "ceos")
        assert len(result) >= 1
        assert any(r["id"] == "docker:ceos:4.28" for r in result)


# ---------------------------------------------------------------------------
# Tests: _resolve_writable_canonical_device_id
# ---------------------------------------------------------------------------

class TestResolveWritableCanonicalDeviceId:
    def test_none_input_returns_none(self, test_db) -> None:
        from app.services.catalog_query import _resolve_writable_canonical_device_id
        assert _resolve_writable_canonical_device_id(test_db, None) is None

    def test_empty_input_returns_none(self, test_db) -> None:
        from app.services.catalog_query import _resolve_writable_canonical_device_id
        assert _resolve_writable_canonical_device_id(test_db, "  ") is None

    def test_returns_canonical_when_exists(self, test_db) -> None:
        from app.services.catalog_query import _resolve_writable_canonical_device_id
        from app import models
        from uuid import uuid4

        vendor = models.CatalogVendor(
            id=str(uuid4()), vendor_key="arista", display_name="Arista",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(vendor)
        test_db.flush()

        device = models.CatalogDeviceType(
            id=str(uuid4()), canonical_device_id="ceos",
            vendor_id=vendor.id, runtime_kind="ceos",
            display_name="cEOS", source="manual",
            lifecycle_status="active", metadata_json="{}",
        )
        test_db.add(device)
        test_db.flush()

        result = _resolve_writable_canonical_device_id(test_db, "ceos")
        assert result == "ceos"

    def test_unknown_token_returned_as_is(self, test_db) -> None:
        from app.services.catalog_query import _resolve_writable_canonical_device_id
        result = _resolve_writable_canonical_device_id(test_db, "brand_new_device")
        assert result == "brand_new_device"


# ---------------------------------------------------------------------------
# Tests: _ensure_unknown_vendor / _ensure_device_type
# ---------------------------------------------------------------------------

class TestEnsureHelpers:
    def test_ensure_unknown_vendor_creates_once(self, test_db) -> None:
        from app.services.catalog_query import _ensure_unknown_vendor
        from app import models

        v1 = _ensure_unknown_vendor(test_db)
        v2 = _ensure_unknown_vendor(test_db)
        assert v1.id == v2.id
        assert v1.vendor_key == "unknown"
        count = test_db.query(models.CatalogVendor).filter(
            models.CatalogVendor.vendor_key == "unknown"
        ).count()
        assert count == 1

    def test_ensure_device_type_creates_with_revision(self, test_db) -> None:
        from app.services.catalog_query import _ensure_device_type
        from app import models

        dt = _ensure_device_type(test_db, "my_new_device")
        assert dt.canonical_device_id == "my_new_device"

        rev = test_db.query(models.CatalogDeviceRevision).filter(
            models.CatalogDeviceRevision.device_type_id == dt.id
        ).first()
        assert rev is not None
        assert rev.is_current is True

    def test_ensure_device_type_idempotent(self, test_db) -> None:
        from app.services.catalog_query import _ensure_device_type

        dt1 = _ensure_device_type(test_db, "my_device")
        dt2 = _ensure_device_type(test_db, "my_device")
        assert dt1.id == dt2.id


# ---------------------------------------------------------------------------
# Tests: delete_catalog_image
# ---------------------------------------------------------------------------

class TestDeleteCatalogImage:
    def test_delete_nonexistent_raises(self, test_db) -> None:
        from app.services.catalog_query import delete_catalog_image
        with pytest.raises(CatalogImageNotFoundError):
            delete_catalog_image(test_db, "ghost-image")

    def test_delete_removes_row_and_records_event(self, test_db) -> None:
        from app.services.catalog_query import delete_catalog_image
        from app import models
        from uuid import uuid4

        img = models.CatalogImage(
            id=str(uuid4()), external_id="to-delete",
            kind="docker", source="api", metadata_json="{}",
        )
        test_db.add(img)
        test_db.flush()

        result = delete_catalog_image(test_db, "to-delete")
        test_db.flush()
        assert result["id"] == "to-delete"

        remaining = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "to-delete"
        ).first()
        assert remaining is None

        event = test_db.query(models.CatalogIngestEvent).filter(
            models.CatalogIngestEvent.event_type == "image_delete"
        ).first()
        assert event is not None


# ---------------------------------------------------------------------------
# Tests: sync_catalog_from_manifest
# ---------------------------------------------------------------------------

class TestSyncCatalogFromManifest:
    def test_sync_adds_images_and_removes_stale(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4

        # Pre-existing image that should be removed
        old_img = models.CatalogImage(
            id=str(uuid4()), external_id="old-image",
            kind="docker", source="api", metadata_json="{}",
        )
        test_db.add(old_img)
        test_db.flush()

        manifest = {
            "images": [
                {"id": "new-image", "kind": "docker", "source": "manifest"},
            ]
        }
        sync_catalog_from_manifest(test_db, manifest)
        test_db.flush()

        old = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "old-image"
        ).first()
        assert old is None

        new = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "new-image"
        ).first()
        assert new is not None

    def test_sync_empty_manifest_clears_all(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4

        test_db.add(models.CatalogImage(
            id=str(uuid4()), external_id="img1",
            kind="docker", source="api", metadata_json="{}",
        ))
        test_db.flush()

        sync_catalog_from_manifest(test_db, {"images": []})
        test_db.flush()

        count = test_db.query(models.CatalogImage).count()
        assert count == 0

    def test_sync_skips_entries_without_id(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models

        manifest = {
            "images": [
                {"kind": "docker"},  # no id
                {"id": "", "kind": "docker"},  # empty id
            ]
        }
        sync_catalog_from_manifest(test_db, manifest)
        test_db.flush()

        count = test_db.query(models.CatalogImage).count()
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: record_catalog_ingest_event
# ---------------------------------------------------------------------------

class TestRecordIngestEvent:
    def test_records_event(self, test_db) -> None:
        from app.services.catalog_query import record_catalog_ingest_event
        from app import models

        record_catalog_ingest_event(
            test_db,
            source="test",
            event_type="test_event",
            summary="Testing",
            payload={"key": "value"},
        )
        test_db.flush()

        event = test_db.query(models.CatalogIngestEvent).first()
        assert event is not None
        assert event.source == "test"
        assert event.event_type == "test_event"
