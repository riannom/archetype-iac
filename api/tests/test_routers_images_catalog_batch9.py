"""Additional catalog-seeded coverage for app.routers.images."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import BackgroundTasks

import pytest
from fastapi import HTTPException

from app.routers import images as img


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="u-1", username="alice")


def _db() -> SimpleNamespace:
    return SimpleNamespace(commit=Mock(), rollback=Mock())


def test_update_image_library_catalog_paths(monkeypatch):
    db = _db()
    monkeypatch.setattr(img, "catalog_is_seeded", lambda _database: True)

    updated_payload = {
        "id": "docker:ceos:4.29.0",
        "device_id": "eos",
        "version": "4.29.0",
    }
    monkeypatch.setattr(
        img,
        "apply_manifest_style_image_update",
        lambda *_args, **_kwargs: updated_payload,
    )

    result = img.update_image_library(
        "docker:ceos:4.29.0",
        {"device_id": "eos", "version": "4.29.0", "notes": "ok"},
        database=db,
        current_user=_user(),
    )
    assert result["image"]["id"] == "docker:ceos:4.29.0"
    db.commit.assert_called_once()

    db_nf = _db()
    monkeypatch.setattr(
        img,
        "apply_manifest_style_image_update",
        Mock(side_effect=img.CatalogImageNotFoundError("missing")),
    )
    with pytest.raises(HTTPException) as exc_info:
        img.update_image_library(
            "missing",
            {"version": "1.0"},
            database=db_nf,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 404
    db_nf.rollback.assert_called_once()

    db_conflict = _db()
    monkeypatch.setattr(
        img,
        "apply_manifest_style_image_update",
        Mock(side_effect=img.CatalogAliasConflictError("alias conflict")),
    )
    with pytest.raises(HTTPException) as exc_info:
        img.update_image_library(
            "docker:ceos:4.29.0",
            {"device_id": "ceos"},
            database=db_conflict,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 400
    db_conflict.rollback.assert_called_once()

    db_error = _db()
    monkeypatch.setattr(
        img,
        "apply_manifest_style_image_update",
        Mock(side_effect=RuntimeError("db exploded")),
    )
    with pytest.raises(RuntimeError, match="db exploded"):
        img.update_image_library(
            "docker:ceos:4.29.0",
            {"device_id": "eos"},
            database=db_error,
            current_user=_user(),
        )
    db_error.rollback.assert_called_once()


def test_assign_image_to_device_catalog_paths(monkeypatch):
    monkeypatch.setattr(img, "catalog_is_seeded", lambda _database: True)

    db = _db()
    monkeypatch.setattr(img, "resolve_catalog_device_id", lambda *_args, **_kwargs: "eos")
    monkeypatch.setattr(
        img,
        "get_catalog_library_image",
        lambda *_args, **_kwargs: {"id": "docker:ceos:4.29.0", "compatible_devices": ["iosv"]},
    )

    captured: dict[str, object] = {}

    def _apply(_database, _image_id, updates, **_kwargs):
        captured["updates"] = updates
        return {"id": "docker:ceos:4.29.0", **updates}

    monkeypatch.setattr(img, "apply_manifest_style_image_update", _apply)

    result = img.assign_image_to_device(
        "docker:ceos:4.29.0",
        {"device_id": "ceos", "is_default": True},
        database=db,
        current_user=_user(),
    )
    assert result["image"]["device_id"] == "eos"
    assert sorted(captured["updates"]["compatible_devices"]) == ["eos", "iosv"]
    assert captured["updates"]["default_for_device"] == "eos"
    db.commit.assert_called_once()

    db_missing = _db()
    monkeypatch.setattr(img, "get_catalog_library_image", lambda *_args, **_kwargs: None)
    with pytest.raises(HTTPException) as exc_info:
        img.assign_image_to_device(
            "docker:missing",
            {"device_id": "ceos"},
            database=db_missing,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 404

    db_alias = _db()
    monkeypatch.setattr(
        img,
        "resolve_catalog_device_id",
        Mock(side_effect=img.CatalogAliasConflictError("bad alias")),
    )
    with pytest.raises(HTTPException) as exc_info:
        img.assign_image_to_device(
            "docker:ceos:4.29.0",
            {"device_id": "ceos"},
            database=db_alias,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 400


def test_unassign_image_from_device_catalog_paths(monkeypatch):
    monkeypatch.setattr(img, "catalog_is_seeded", lambda _database: True)

    db = _db()

    def _resolve(_database, device_id, allow_unknown=True):  # noqa: ARG001
        if device_id == "ceos":
            return "eos"
        return device_id

    monkeypatch.setattr(img, "resolve_catalog_device_id", _resolve)
    monkeypatch.setattr(
        img,
        "get_catalog_library_image",
        lambda *_args, **_kwargs: {
            "id": "docker:ceos:4.29.0",
            "device_id": "eos",
            "compatible_devices": ["eos", "iosv"],
        },
    )

    captured: dict[str, object] = {}

    def _apply(_database, _image_id, updates, **_kwargs):
        captured["updates"] = updates
        return {"id": "docker:ceos:4.29.0", **updates}

    monkeypatch.setattr(img, "apply_manifest_style_image_update", _apply)

    result = img.unassign_image_from_device(
        "docker:ceos:4.29.0",
        payload={"device_id": "ceos"},
        database=db,
        current_user=_user(),
    )
    assert result["image"]["device_id"] == "ceos"
    assert "ceos" in result["image"]["compatible_devices"]
    assert any("iosv" in dev for dev in result["image"]["compatible_devices"])
    assert captured["updates"]["default_for_device"] == "ceos"
    db.commit.assert_called_once()

    db_full = _db()
    monkeypatch.setattr(img, "apply_manifest_style_image_update", _apply)
    full = img.unassign_image_from_device(
        "docker:ceos:4.29.0",
        payload=None,
        database=db_full,
        current_user=_user(),
    )
    assert full["image"]["device_id"] is None
    assert full["image"]["compatible_devices"] == []
    assert full["image"]["default_for_devices"] == []
    db_full.commit.assert_called_once()

    db_alias = _db()
    monkeypatch.setattr(
        img,
        "resolve_catalog_device_id",
        Mock(side_effect=img.CatalogAliasConflictError("alias conflict")),
    )
    with pytest.raises(HTTPException) as exc_info:
        img.unassign_image_from_device(
            "docker:ceos:4.29.0",
            payload={"device_id": "ceos"},
            database=db_alias,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 400

    db_not_found = _db()
    monkeypatch.setattr(img, "resolve_catalog_device_id", _resolve)
    monkeypatch.setattr(
        img,
        "apply_manifest_style_image_update",
        Mock(side_effect=img.CatalogImageNotFoundError("missing")),
    )
    with pytest.raises(HTTPException) as exc_info:
        img.unassign_image_from_device(
            "docker:ceos:4.29.0",
            payload=None,
            database=db_not_found,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 404
    db_not_found.rollback.assert_called_once()


def test_delete_image_catalog_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(img, "catalog_is_seeded", lambda _database: True)

    # Success: qcow2 path exists and is removed.
    db = _db()
    qcow2_file = tmp_path / "device.qcow2"
    qcow2_file.write_bytes(b"qcow2")

    monkeypatch.setattr(
        img,
        "get_catalog_library_image",
        lambda *_args, **_kwargs: {
            "id": "qcow2:device.qcow2",
            "kind": "qcow2",
            "reference": str(qcow2_file),
        },
    )
    delete_calls: list[str] = []
    monkeypatch.setattr(img, "delete_catalog_image", lambda *_args: delete_calls.append("deleted"))

    result = img.delete_image(
        "qcow2:device.qcow2",
        background_tasks=BackgroundTasks(),
        database=db,
        current_user=_user(),
    )
    assert "deleted successfully" in result["message"]
    assert delete_calls == ["deleted"]
    assert not qcow2_file.exists()
    db.commit.assert_called_once()

    # OSError while deleting file -> 500.
    db_unlink = _db()
    qcow2_file2 = tmp_path / "device2.qcow2"
    qcow2_file2.write_bytes(b"qcow2")
    monkeypatch.setattr(
        img,
        "get_catalog_library_image",
        lambda *_args, **_kwargs: {
            "id": "qcow2:device2.qcow2",
            "kind": "qcow2",
            "reference": str(qcow2_file2),
        },
    )

    def _raise_unlink(self, missing_ok=False):  # noqa: ARG001
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", _raise_unlink)
    with pytest.raises(HTTPException) as exc_info:
        img.delete_image(
            "qcow2:device2.qcow2",
            background_tasks=BackgroundTasks(),
            database=db_unlink,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 500

    # Catalog delete race -> rollback + 404.
    db_nf = _db()
    monkeypatch.setattr(Path, "unlink", Path.unlink)
    monkeypatch.setattr(
        img,
        "get_catalog_library_image",
        lambda *_args, **_kwargs: {
            "id": "docker:ceos:4.29.0",
            "kind": "docker",
            "reference": "ceos:4.29.0",
        },
    )
    monkeypatch.setattr(
        img,
        "delete_catalog_image",
        Mock(side_effect=img.CatalogImageNotFoundError("gone")),
    )

    with pytest.raises(HTTPException) as exc_info:
        img.delete_image(
            "docker:ceos:4.29.0",
            background_tasks=BackgroundTasks(),
            database=db_nf,
            current_user=_user(),
        )
    assert exc_info.value.status_code == 404
    db_nf.rollback.assert_called_once()
