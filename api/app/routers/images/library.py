"""Image library management endpoints: list, update, assign, unassign, delete."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_admin, get_current_user
from app.image_store import (
    canonicalize_device_id,
    canonicalize_device_ids,
    delete_image_entry,
    ensure_image_store,
    find_image_by_id,
    image_matches_device,
    load_manifest,
    save_manifest,
    update_image_entry,
)
from app.services.catalog_service import (
    CatalogAliasConflictError,
    CatalogImageNotFoundError,
    apply_manifest_style_image_update,
    catalog_is_seeded,
    delete_catalog_image,
    get_catalog_library_image,
    list_catalog_images_for_device,
    list_catalog_library_images,
    resolve_catalog_device_id,
)
from app.utils.image_integrity import compute_sha256

router = APIRouter(tags=["images"])


@router.post("/backfill-checksums")
def backfill_checksums(
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Compute and backfill SHA256 checksums for existing qcow2 images.

    Only processes images that don't already have a sha256 field.
    """


    manifest = load_manifest()
    updated = 0
    errors = []

    for image in manifest.get("images", []):
        if image.get("sha256"):
            continue
        if image.get("kind") not in ("qcow2",):
            continue

        ref = image.get("reference", "")
        if not ref or not os.path.exists(ref):
            errors.append(f"{image.get('id')}: file not found at {ref}")
            continue

        try:
            image["sha256"] = compute_sha256(ref)
            updated += 1
        except Exception as e:
            errors.append(f"{image.get('id')}: {e}")

    if updated:
        save_manifest(manifest)

    return {"updated": updated, "errors": errors}


@router.get("/qcow2")
def list_qcow2(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, str]]]:
    root = ensure_image_store()
    files = []
    for path in sorted(root.glob("*.qcow2")) + sorted(root.glob("*.qcow")):
        files.append({"filename": path.name, "path": str(path)})
    return {"files": files}


@router.get("/library")
def list_image_library(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, object]]]:
    if catalog_is_seeded(database):
        return {"images": list_catalog_library_images(database)}

    manifest = load_manifest()
    return {"images": manifest.get("images", [])}


@router.post("/library/{image_id}")
def update_image_library(
    image_id: str,
    payload: dict,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Update an image's metadata (device_id, version, notes, is_default, etc.).

    Requires admin access.
    """

    # Build updates from payload
    updates = {}
    if "device_id" in payload:
        updates["device_id"] = payload["device_id"]
    if "version" in payload:
        updates["version"] = payload["version"]
    if "notes" in payload:
        updates["notes"] = payload["notes"]
    if "is_default" in payload:
        updates["is_default"] = payload["is_default"]
    if "compatible_devices" in payload:
        updates["compatible_devices"] = payload["compatible_devices"]

    if catalog_is_seeded(database):
        try:
            updated = apply_manifest_style_image_update(
                database,
                image_id,
                updates,
                event_type="image_update",
                summary=f"Updated image '{image_id}' metadata",
            )
            database.commit()
        except CatalogImageNotFoundError:
            database.rollback()
            raise HTTPException(status_code=404, detail="Image not found")
        except CatalogAliasConflictError as exc:
            database.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            database.rollback()
            raise

        return {"image": updated}

    manifest = load_manifest()
    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.post("/library/{image_id}/assign")
def assign_image_to_device(
    image_id: str,
    payload: dict,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Assign an image to a device type.

    Body: { "device_id": "eos", "is_default": true }
    Requires admin access.
    """

    device_id = payload.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    resolved_device_id = device_id
    if catalog_is_seeded(database):
        try:
            resolved_device_id = (
                resolve_catalog_device_id(database, device_id, allow_unknown=True)
                or device_id
            )
        except CatalogAliasConflictError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    is_default = bool(payload.get("is_default", False))

    updates = {"device_id": resolved_device_id}
    if is_default:
        updates["is_default"] = True
        updates["default_for_device"] = resolved_device_id

    if catalog_is_seeded(database):
        image = get_catalog_library_image(database, image_id, force_refresh=True)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")
        compatible = list(image.get("compatible_devices") or [])
        if resolved_device_id not in compatible:
            compatible.append(resolved_device_id)
        updates["compatible_devices"] = compatible

        try:
            updated = apply_manifest_style_image_update(
                database,
                image_id,
                updates,
                event_type="image_assign",
                summary=f"Assigned image '{image_id}' to '{resolved_device_id}'",
            )
            database.commit()
        except CatalogAliasConflictError as exc:
            database.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
        except CatalogImageNotFoundError:
            database.rollback()
            raise HTTPException(status_code=404, detail="Image not found")
        except Exception:
            database.rollback()
            raise

        return {"image": updated}

    manifest = load_manifest()

    # Add to compatible_devices if not already there
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            compatible = item.get("compatible_devices", [])
            if device_id not in compatible:
                compatible.append(device_id)
            updates["compatible_devices"] = compatible
            break

    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.post("/library/{image_id}/unassign")
def unassign_image_from_device(
    image_id: str,
    payload: dict | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Unassign an image from its current device type.

    Requires admin access.
    """

    target_device_id = payload.get("device_id") if isinstance(payload, dict) else None

    if catalog_is_seeded(database):
        image = get_catalog_library_image(database, image_id, force_refresh=True)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        if target_device_id:
            try:
                target_canonical = (
                    resolve_catalog_device_id(database, target_device_id, allow_unknown=True)
                    or target_device_id
                )
            except CatalogAliasConflictError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            compatible = canonicalize_device_ids(image.get("compatible_devices") or [])
            compatible = [dev for dev in compatible if dev != target_canonical]

            current_primary = (
                resolve_catalog_device_id(database, image.get("device_id"), allow_unknown=True)
                if image.get("device_id")
                else None
            )
            if compatible:
                new_primary = current_primary if current_primary in compatible else compatible[0]
            else:
                new_primary = None

            # Device-scoped unassign removes the target from compatibility and
            # clears default binding only for that device scope.
            updates: dict[str, object] = {
                "device_id": new_primary,
                "compatible_devices": compatible,
                "is_default": False,
                "default_for_device": target_device_id,
            }
        else:
            # Full unassign clears all compatibility and default bindings.
            updates = {
                "device_id": None,
                "compatible_devices": [],
                "default_for_devices": [],
            }

        try:
            updated = apply_manifest_style_image_update(
                database,
                image_id,
                updates,
                event_type="image_unassign",
                summary=f"Unassigned image '{image_id}'",
            )
            database.commit()
        except CatalogAliasConflictError as exc:
            database.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
        except CatalogImageNotFoundError:
            database.rollback()
            raise HTTPException(status_code=404, detail="Image not found")
        except Exception:
            database.rollback()
            raise

        return {"image": updated}

    manifest = load_manifest()

    if target_device_id:
        image = find_image_by_id(manifest, image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        target_canonical = canonicalize_device_id(target_device_id)
        compatible = canonicalize_device_ids(image.get("compatible_devices") or [])
        if target_canonical:
            compatible = [dev for dev in compatible if dev != target_canonical]

        current_primary = canonicalize_device_id(image.get("device_id"))
        if compatible:
            new_primary = current_primary if current_primary in compatible else compatible[0]
        else:
            new_primary = None

        # Device-scoped unassign removes the target from compatibility and
        # clears default binding only for that device scope.
        updates = {
            "device_id": new_primary,
            "compatible_devices": compatible,
            "is_default": False,
            "default_for_device": target_device_id,
        }
    else:
        # Full unassign clears all compatibility and default bindings.
        updates = {
            "device_id": None,
            "compatible_devices": [],
            "default_for_devices": [],
        }

    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.delete("/library/{image_id}")
def delete_image(
    image_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, str]:
    """Delete an image from the library.

    For QCOW2 images, also deletes the file from disk.
    For Docker images, only removes from manifest (does not remove from Docker).
    Requires admin access.
    """

    if catalog_is_seeded(database):
        image = get_catalog_library_image(database, image_id, force_refresh=True)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        # If it's a QCOW2 image, delete the file from disk
        if image.get("kind") == "qcow2":
            file_path = Path(image.get("reference", ""))
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError as e:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to delete file: {e}"
                    )

        try:
            delete_catalog_image(database, image_id)
            database.commit()
        except CatalogImageNotFoundError:
            database.rollback()
            raise HTTPException(status_code=404, detail="Image not found")
        except Exception:
            database.rollback()
            raise

        return {"message": f"Image '{image_id}' deleted successfully"}

    manifest = load_manifest()

    # Find the image first to get its details
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # If it's a QCOW2 image, delete the file from disk
    if image.get("kind") == "qcow2":
        file_path = Path(image.get("reference", ""))
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete file: {e}"
                )

    # Remove from manifest
    deleted = delete_image_entry(manifest, image_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"message": f"Image '{image_id}' deleted successfully"}


@router.get("/devices/{device_id}/images")
def get_images_for_device(
    device_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict]]:
    """Get all images assigned to or compatible with a device type."""
    if catalog_is_seeded(database):
        return {"images": list_catalog_images_for_device(database, device_id)}

    manifest = load_manifest()
    images = [img for img in manifest.get("images", [])
              if image_matches_device(img, device_id)]
    return {"images": images}
