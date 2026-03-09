# Docker Image Archive Phase 3 Design

**Created:** 2026-03-09  
**Status:** Proposed  
**Scope:** Catalog-backed durable archives for Docker library images

## Goal

Make Docker image sync resilient to controller-side Docker image-store loss by
keeping a durable file-based archive for each uploaded Docker image and teaching
the sync path to prefer that archive over live `docker save`.

## Problem

Today Docker images are effectively durable only inside the controller's Docker
daemon. If the controller loses its daemon image store, image sync can fail even
when the library entry still exists in the catalog.

This is different from qcow2 and IOL artifacts, which already have a durable
file representation on disk.

## Non-Goals

- Changing how agent-side image receipt works
- Replacing the catalog as the source of truth for image-library metadata
- Implementing content-addressed dedup across unrelated Docker images
- Auto-rebuilding corrupted archives without operator intent

## Design Decision

Phase 3 must be catalog-backed, not manifest-only.

The repo now reads the image library from catalog tables when seeded, and
`manifest.json` is a compatibility projection. Archive state therefore belongs
in the catalog image record, with optional projection into manifest-style views.

## Archive Definition

An archive is a tar file containing the serialized Docker image payload for one
catalog image, stored on the controller filesystem under a deterministic path.

Example:

`/var/lib/archetype/images/archives/docker__ceos__4.35.1f.tar`

The archive becomes the durable source used for later syncs when available.

## Storage Model

### Filesystem layout

Use a dedicated archive directory under the existing image store root:

- base: `Path(settings.workspace) / "images" / "archives"`
- one archive per Docker library image
- temporary files written to the same directory with a `.partial` suffix

Recommended helper additions in [`api/app/image_store/paths.py`](/home/azayaka/archetype-iac/api/app/image_store/paths.py):

- `docker_archive_root() -> Path`
- `ensure_docker_archive_root() -> Path`
- `docker_archive_path(image_id: str) -> Path`

Filename rule:

- deterministic from `image_id`
- ASCII-safe
- no direct reuse of raw Docker reference as the filename

Suggested encoding:

- replace non-alphanumeric characters with `_`
- append `.tar`

### Catalog metadata

Store archive state on [`CatalogImage`](/home/azayaka/archetype-iac/api/app/models/catalog.py) as first-class columns, not only inside `metadata_json`.

Add columns:

- `archive_path: String(500) | NULL`
- `archive_status: String(20) | NOT NULL default 'none'`
- `archive_sha256: String(128) | NULL`
- `archive_size_bytes: BigInteger | NULL`
- `archive_created_at: DateTime(timezone=True) | NULL`
- `archive_verified_at: DateTime(timezone=True) | NULL`
- `archive_error: Text | NULL`

Allowed `archive_status` values:

- `none`
- `pending`
- `ready`
- `failed`

Reason for explicit columns instead of `metadata_json` only:

- queryability
- operational visibility
- easier integrity reporting
- cleaner support-bundle output

`metadata_json` can still mirror these values for manifest-style compatibility.

## Configuration

Add to [`api/app/config.py`](/home/azayaka/archetype-iac/api/app/config.py):

- `image_archive_docker_images: bool = True`
- `image_archive_verify_interval_cycles: int = 6`
- `image_archive_max_parallel_jobs: int = 1`

Optional later:

- `image_archive_root: str | None = None`

Default behavior:

- archive creation enabled
- verification every sixth reconciliation cycle
- single archive job at a time to limit disk and CPU pressure

## Runtime Flow

### 1. Archive on upload/import

Trigger archive creation after successful Docker upload/import in
[`api/app/routers/images/upload_docker.py`](/home/azayaka/archetype-iac/api/app/routers/images/upload_docker.py).

Flow:

1. Docker image is successfully loaded or imported
2. Catalog/library entry exists
3. Mark archive state `pending`
4. Start a background archive job
5. Run `docker save <reference>` to a `.partial` path
6. Compute SHA256 and size
7. Atomically rename to final `.tar`
8. Mark archive state `ready`

Failure handling:

- leave library image usable
- set archive state `failed`
- record `archive_error`
- remove partial file

Important:

- upload success must not depend on archive success
- archive creation is additive, not part of the critical upload transaction

### 2. Prefer archive during sync

Update [`api/app/routers/images/sync.py`](/home/azayaka/archetype-iac/api/app/routers/images/sync.py) so Docker sync does:

1. If catalog image has `archive_status == 'ready'`
2. Verify `archive_path` exists
3. Stream the archive file directly to the agent
4. Fall back to live `docker save` only when no valid archive exists

Behavioral rule:

- archive path is preferred, not required
- live `docker save` remains fallback for backward compatibility and recovery

Implementation note:

Refactor the current Docker branch of `_execute_sync_job()` into two paths:

- `_stream_docker_archive_to_agent(...)`
- `_stream_docker_save_to_agent(...)`

This keeps failure modes explicit.

### 3. Periodic archive verification

Extend [`api/app/tasks/image_reconciliation.py`](/home/azayaka/archetype-iac/api/app/tasks/image_reconciliation.py) with a catalog-backed archive integrity audit.

Run every `image_archive_verify_interval_cycles`.

Checks:

- `archive_status == 'ready'`
- file exists
- file size matches
- SHA256 matches

On failure:

- set `archive_status = 'failed'`
- set `archive_error`
- clear `archive_verified_at`

Do not auto-recreate during periodic verification.

### 4. Manual backfill/archive endpoint

Add an operator endpoint:

- `POST /images/library/{image_id}/archive`

Behavior:

- if archive is `ready`, return current metadata
- if archive is `pending`, return accepted/in-progress state
- if archive is `none` or `failed`, queue archive creation

This is the operator path for existing images created before Phase 3 lands.

## API Shape

### Library projection

Extend library responses from catalog query code so each Docker image can expose:

- `archive_status`
- `archive_size_bytes`
- `archive_created_at`
- `archive_verified_at`
- `archive_error`

Do not expose raw filesystem paths to non-admin clients by default.

For admin-only detail views, `archive_path` can be exposed if needed.

### Sync observability

When a sync uses an archive, log and optionally expose:

- `archive_used=true`
- `archive_path` only in logs/support bundle, not normal user payloads

## Model and Query Changes

### Database

Files:

- [`api/app/models/catalog.py`](/home/azayaka/archetype-iac/api/app/models/catalog.py)
- new Alembic revision after `060`

### Catalog projection

Files:

- [`api/app/services/catalog_query.py`](/home/azayaka/archetype-iac/api/app/services/catalog_query.py)

Required updates:

- persist archive fields from manifest-style payloads into catalog columns
- include archive fields in projected library images
- keep `metadata_json` projection in sync

### Image store paths

Files:

- [`api/app/image_store/paths.py`](/home/azayaka/archetype-iac/api/app/image_store/paths.py)

## Background Job Model

Keep initial implementation simple:

- no new DB job table
- archive creation runs as fire-and-forget background work from upload/manual endpoint
- archive state is tracked on `CatalogImage`

If this grows operationally expensive later, introduce a dedicated
`ImageArchiveJob` model. That is not required for the first implementation.

## Failure Semantics

### Upload succeeds, archive fails

- library image remains valid
- sync can still use live `docker save`
- operator sees `archive_status=failed`

### Archive exists, file missing

- next verification marks archive `failed`
- sync falls back to live `docker save`

### Archive exists, controller Docker image missing

- sync still succeeds through archive path

### Archive corrupt, Docker image missing

- sync fails
- this is the scenario the operator must recover with manual re-archive or re-upload

## Rollout Plan

### Phase 3A - Schema and projection

1. Add catalog columns + migration
2. Update catalog projection helpers
3. Add path helpers

### Phase 3B - Archive creation

1. Add archive creation helper
2. Trigger it after successful Docker upload/import
3. Add admin backfill endpoint

### Phase 3C - Sync path preference

1. Teach `_execute_sync_job()` to prefer archive files
2. Keep live `docker save` fallback
3. Add structured logs

### Phase 3D - Verification

1. Add periodic integrity audit
2. Surface archive failures in admin/library views and support bundle output

## Tests

### Unit tests

- archive path generation is deterministic and safe
- archive state projection round-trips through catalog query helpers
- upload success with archive failure leaves image usable
- sync uses archive when `ready`
- sync falls back to live `docker save` when archive is absent
- verification marks missing/corrupt archive as `failed`

### Integration tests

- upload Docker image -> archive becomes `ready`
- remove controller Docker image -> sync still succeeds via archive
- corrupt archive -> sync falls back if Docker image still exists
- manual archive endpoint backfills archive for legacy image

## Open Questions

1. Should `archive_path` be absolute, or derived from `image_id` plus root at runtime?
   Recommendation: persist absolute path for operational clarity, but derive it
   through a helper and keep it under the archive root invariant.

2. Should archives be gzip-compressed?
   Recommendation: no for first version. Raw tar keeps `docker load` and
   streaming behavior simple and avoids extra CPU overhead.

3. Should archive creation be serialized globally?
   Recommendation: yes initially, via `image_archive_max_parallel_jobs = 1`.

## Recommendation

This phase is worth doing, but only with the catalog-backed design above.

Do not implement a manifest-only archive feature. The right first implementation
is:

1. catalog columns
2. archive creation after upload
3. sync path preference
4. verification and admin backfill
