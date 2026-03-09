"""Tests for app.services.catalog_query — index build, cache, query, persistence."""
from __future__ import annotations

from collections import defaultdict
import json
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
                     size_bytes: int | None = None, imported_at=None,
                     archive_path: str | None = None, archive_status: str = "none",
                     archive_sha256: str | None = None, archive_size_bytes: int | None = None,
                     archive_created_at=None, archive_verified_at=None, archive_error: str | None = None):
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
        archive_path=archive_path,
        archive_status=archive_status,
        archive_sha256=archive_sha256,
        archive_size_bytes=archive_size_bytes,
        archive_created_at=archive_created_at,
        archive_verified_at=archive_verified_at,
        archive_error=archive_error,
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

    def test_get_projects_archive_metadata_without_archive_path(self, test_db) -> None:
        from app.services.catalog_query import get_catalog_library_image
        from app import models
        from uuid import uuid4
        from datetime import datetime, UTC

        test_db.add(models.CatalogImage(
            id=str(uuid4()),
            external_id="archived-image",
            kind="docker",
            source="api",
            metadata_json='{"archive_path":"/var/lib/archetype/images/archives/archived-image.tar"}',
            archive_path="/var/lib/archetype/images/archives/archived-image.tar",
            archive_status="ready",
            archive_sha256="deadbeef",
            archive_size_bytes=4096,
            archive_created_at=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
            archive_verified_at=datetime(2026, 3, 9, 13, 0, tzinfo=UTC),
            archive_error=None,
        ))
        test_db.flush()

        result = get_catalog_library_image(test_db, "archived-image", force_refresh=True)
        assert result is not None
        assert result["archive_status"] == "ready"
        assert result["archive_sha256"] == "deadbeef"
        assert result["archive_size_bytes"] == 4096
        assert result["archive_created_at"] == "2026-03-09T12:00:00Z"
        assert result["archive_verified_at"] == "2026-03-09T13:00:00Z"
        assert "archive_path" not in result


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

    def test_sync_with_prune_disabled_keeps_unmentioned_rows(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4

        test_db.add(models.CatalogImage(
            id=str(uuid4()), external_id="kept-image",
            kind="docker", source="api", metadata_json="{}",
        ))
        test_db.flush()

        manifest = {
            "images": [
                {"id": "new-image", "kind": "docker", "source": "manifest"},
            ]
        }
        sync_catalog_from_manifest(test_db, manifest, prune_missing=False)
        test_db.flush()

        kept = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "kept-image"
        ).first()
        new = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "new-image"
        ).first()
        assert kept is not None
        assert new is not None

    def test_sync_records_prune_event_payload(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4
        import json

        test_db.add(models.CatalogImage(
            id=str(uuid4()), external_id="stale-image",
            kind="docker", source="api", metadata_json="{}",
        ))
        test_db.flush()

        sync_catalog_from_manifest(
            test_db,
            {"images": [{"id": "fresh-image", "kind": "docker"}]},
            source="unit-test",
            prune_missing=True,
        )
        test_db.flush()

        event = (
            test_db.query(models.CatalogIngestEvent)
            .filter(models.CatalogIngestEvent.source == "unit-test")
            .filter(models.CatalogIngestEvent.event_type == "manifest_sync")
            .order_by(models.CatalogIngestEvent.created_at.desc())
            .first()
        )
        assert event is not None
        payload = json.loads(event.payload_json)
        assert payload["prune_missing"] is True
        assert payload["deleted_count"] == 1
        assert payload["image_count"] == 1

    def test_sync_records_merge_event_payload_without_deletes(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4
        import json

        test_db.add(models.CatalogImage(
            id=str(uuid4()), external_id="kept-image",
            kind="docker", source="api", metadata_json="{}",
        ))
        test_db.flush()

        sync_catalog_from_manifest(
            test_db,
            {"images": [{"id": "fresh-image", "kind": "docker"}]},
            source="unit-test",
            prune_missing=False,
        )
        test_db.flush()

        event = (
            test_db.query(models.CatalogIngestEvent)
            .filter(models.CatalogIngestEvent.source == "unit-test")
            .filter(models.CatalogIngestEvent.event_type == "manifest_sync")
            .order_by(models.CatalogIngestEvent.created_at.desc())
            .first()
        )
        assert event is not None
        payload = json.loads(event.payload_json)
        assert payload["prune_missing"] is False
        assert payload["deleted_count"] == 0
        assert payload["image_count"] == 1

    def test_sync_with_prune_disabled_empty_payload_keeps_existing_rows(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4

        test_db.add(models.CatalogImage(
            id=str(uuid4()),
            external_id="kept-image",
            kind="docker",
            source="api",
            metadata_json='{"reference":"kept:1.0"}',
            reference="kept:1.0",
        ))
        test_db.flush()

        sync_catalog_from_manifest(test_db, {"images": []}, prune_missing=False)
        test_db.flush()

        kept = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "kept-image"
        ).first()
        assert kept is not None
        assert kept.reference == "kept:1.0"

    def test_sync_with_prune_disabled_updates_existing_row_without_duplication(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4

        existing = models.CatalogImage(
            id=str(uuid4()),
            external_id="docker:ceos:1.0",
            kind="docker",
            source="api",
            metadata_json='{"reference":"ceos:1.0"}',
            reference="ceos:1.0",
            filename="old.tar",
        )
        test_db.add(existing)
        test_db.flush()

        sync_catalog_from_manifest(
            test_db,
            {
                "images": [
                    {
                        "id": "docker:ceos:1.0",
                        "kind": "docker",
                        "reference": "ceos:2.0",
                        "filename": "new.tar",
                        "source": "manifest",
                    }
                ]
            },
            prune_missing=False,
        )
        test_db.flush()

        rows = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "docker:ceos:1.0"
        ).all()
        assert len(rows) == 1
        assert rows[0].reference == "ceos:2.0"
        assert rows[0].filename == "new.tar"

    def test_sync_persists_archive_metadata_to_columns_and_metadata_json(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models

        sync_catalog_from_manifest(
            test_db,
            {
                "images": [
                    {
                        "id": "docker:ceos:archive",
                        "kind": "docker",
                        "source": "manifest",
                        "archive_path": "/var/lib/archetype/images/archives/docker__ceos__archive.tar",
                        "archive_status": "ready",
                        "archive_sha256": "abc123",
                        "archive_size_bytes": 2048,
                        "archive_created_at": "2026-03-09T14:00:00Z",
                        "archive_verified_at": "2026-03-09T15:00:00Z",
                        "archive_error": None,
                    }
                ]
            },
            prune_missing=False,
        )
        test_db.flush()

        row = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "docker:ceos:archive"
        ).one()
        assert row.archive_path == "/var/lib/archetype/images/archives/docker__ceos__archive.tar"
        assert row.archive_status == "ready"
        assert row.archive_sha256 == "abc123"
        assert row.archive_size_bytes == 2048
        metadata = json.loads(row.metadata_json)
        assert metadata["archive_path"] == row.archive_path
        assert metadata["archive_status"] == "ready"
        assert metadata["archive_sha256"] == "abc123"

    def test_sync_without_archive_keys_preserves_existing_archive_metadata(self, test_db) -> None:
        from app.services.catalog_query import sync_catalog_from_manifest
        from app import models
        from uuid import uuid4
        from datetime import datetime, UTC

        existing = models.CatalogImage(
            id=str(uuid4()),
            external_id="docker:ceos:preserve",
            kind="docker",
            source="api",
            metadata_json="{}",
            archive_path="/var/lib/archetype/images/archives/docker__ceos__preserve.tar",
            archive_status="ready",
            archive_sha256="preserved",
            archive_size_bytes=8192,
            archive_created_at=datetime(2026, 3, 9, 10, 0, tzinfo=UTC),
            archive_verified_at=datetime(2026, 3, 9, 11, 0, tzinfo=UTC),
            archive_error=None,
        )
        test_db.add(existing)
        test_db.flush()

        sync_catalog_from_manifest(
            test_db,
            {
                "images": [
                    {
                        "id": "docker:ceos:preserve",
                        "kind": "docker",
                        "reference": "ceos:3.0",
                        "source": "manifest",
                    }
                ]
            },
            prune_missing=False,
        )
        test_db.flush()

        row = test_db.query(models.CatalogImage).filter(
            models.CatalogImage.external_id == "docker:ceos:preserve"
        ).one()
        assert row.reference == "ceos:3.0"
        assert row.archive_path == "/var/lib/archetype/images/archives/docker__ceos__preserve.tar"
        assert row.archive_status == "ready"
        assert row.archive_sha256 == "preserved"
        assert row.archive_size_bytes == 8192


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
