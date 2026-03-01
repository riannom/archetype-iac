"""Tests for image upload, build, checksum, push, and stream endpoints."""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(images: list[dict] | None = None) -> dict:
    """Build a minimal manifest dict."""
    return {"version": 1, "images": images or []}


def _qcow2_image(
    tmp_path: Path,
    filename: str = "veos-4.29.qcow2",
    device_id: str = "eos",
    version: str = "4.29",
) -> dict:
    """Return a manifest image entry for a qcow2 with a real file on disk."""
    fp = tmp_path / filename
    fp.write_bytes(b"\x00" * 64)
    return {
        "id": f"qcow2:{filename}",
        "kind": "qcow2",
        "reference": str(fp),
        "filename": filename,
        "device_id": device_id,
        "version": version,
    }


def _docker_image(
    reference: str = "ceos:4.28.0",
    device_id: str = "eos",
    version: str = "4.28.0",
) -> dict:
    return {
        "id": f"docker:{reference}",
        "kind": "docker",
        "reference": reference,
        "filename": f"{reference.replace(':', '-')}.tar",
        "device_id": device_id,
        "version": version,
    }


# ---------------------------------------------------------------------------
# TestLoadImageSync — POST /images/load (synchronous, no background/stream)
# ---------------------------------------------------------------------------


class TestLoadImageSync:
    """Tests for POST /images/load (synchronous mode)."""

    def _patch_subprocess(self, monkeypatch, *, returncode=0, stdout="", stderr=""):
        """Replace subprocess.run inside the images router."""
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        monkeypatch.setattr(
            "app.routers.images.subprocess.run",
            lambda *a, **kw: result,
        )
        return result

    def _patch_disk_pressure(self, monkeypatch, *, critical=False):
        from app.services.resource_monitor import PressureLevel

        level = PressureLevel.CRITICAL if critical else PressureLevel.NORMAL
        monkeypatch.setattr(
            "app.routers.images.ResourceMonitor.check_disk_pressure",
            staticmethod(lambda: level),
        )

    def test_docker_tar_upload_success(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Uploading a valid Docker tar should succeed and register the image."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: True)

        sub_result = MagicMock()
        sub_result.returncode = 0
        sub_result.stdout = "Loaded image: ceos:4.28.0\n"
        sub_result.stderr = ""
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        manifest = _make_manifest()
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)
        monkeypatch.setattr(
            img,
            "detect_device_from_filename",
            lambda _: ("eos", "4.28.0"),
        )
        monkeypatch.setattr(
            img,
            "create_image_entry",
            lambda **kw: {"id": kw["image_id"], **kw},
        )

        tar_content = b"fake tar content"
        response = test_client.post(
            "/images/load",
            files={"file": ("ceos.tar", io.BytesIO(tar_content), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert "ceos:4.28.0" in data["images"]

    def test_raw_import_non_docker(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """A raw filesystem tar should trigger docker import and succeed."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: False)

        sub_result = MagicMock()
        sub_result.returncode = 0
        sub_result.stdout = "sha256:abc123\n"
        sub_result.stderr = ""
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        manifest = _make_manifest()
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)
        monkeypatch.setattr(
            img, "detect_device_from_filename", lambda _: (None, None)
        )
        monkeypatch.setattr(
            img,
            "create_image_entry",
            lambda **kw: {"id": kw["image_id"], **kw},
        )

        response = test_client.post(
            "/images/load",
            files={"file": ("rootfs.tar", io.BytesIO(b"data"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "images" in data
        assert any("imported" in img_ref for img_ref in data["images"])

    def test_xz_decompressed_file(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """An .xz file that fails decompression should return 400."""

        self._patch_disk_pressure(monkeypatch)

        # Provide non-LZMA data so decompression fails
        response = test_client.post(
            "/images/load",
            files={
                "file": (
                    "image.tar.xz",
                    io.BytesIO(b"not-xz-data"),
                    "application/x-tar",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "decompress" in response.json()["detail"].lower()

    def test_docker_load_failure(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """When docker load returns non-zero, the endpoint should return 500."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: True)

        sub_result = MagicMock()
        sub_result.returncode = 1
        sub_result.stdout = ""
        sub_result.stderr = "Error: invalid archive"
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        response = test_client.post(
            "/images/load",
            files={"file": ("bad.tar", io.BytesIO(b"bad"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 500

    def test_disk_pressure_blocks_upload(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Critical disk pressure should reject the upload with 507."""
        self._patch_disk_pressure(monkeypatch, critical=True)

        response = test_client.post(
            "/images/load",
            files={"file": ("img.tar", io.BytesIO(b"x"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 507
        assert "disk" in response.json()["detail"].lower()

    def test_manifest_update_after_load(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """After a successful load the manifest should contain the new image."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: True)

        sub_result = MagicMock()
        sub_result.returncode = 0
        sub_result.stdout = "Loaded image: srlinux:24.3\n"
        sub_result.stderr = ""
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        saved_manifests: list[dict] = []
        manifest = _make_manifest()
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: saved_manifests.append(m))
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)
        monkeypatch.setattr(
            img, "detect_device_from_filename", lambda _: ("srlinux", "24.3")
        )
        monkeypatch.setattr(
            img,
            "create_image_entry",
            lambda **kw: {"id": kw["image_id"], **kw},
        )

        response = test_client.post(
            "/images/load",
            files={"file": ("srlinux.tar", io.BytesIO(b"data"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        assert len(saved_manifests) == 1
        assert any(
            e["id"] == "docker:srlinux:24.3" for e in saved_manifests[0]["images"]
        )

    def test_duplicate_image_handling(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Uploading a duplicate Docker image should return 409."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: True)

        sub_result = MagicMock()
        sub_result.returncode = 0
        sub_result.stdout = "Loaded image: ceos:4.28.0\n"
        sub_result.stderr = ""
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        existing = _docker_image()
        manifest = _make_manifest([existing])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.post(
            "/images/load",
            files={"file": ("ceos.tar", io.BytesIO(b"dup"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    def test_no_images_detected(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """When docker load produces no image references, should return 500."""
        from app.routers import images as img

        self._patch_disk_pressure(monkeypatch)
        monkeypatch.setattr(img, "_is_docker_image_tar", lambda _: True)

        sub_result = MagicMock()
        sub_result.returncode = 0
        sub_result.stdout = ""  # No "Loaded image:" lines
        sub_result.stderr = ""
        monkeypatch.setattr(img.subprocess, "run", lambda *a, **kw: sub_result)

        response = test_client.post(
            "/images/load",
            files={"file": ("empty.tar", io.BytesIO(b"x"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 500
        assert "no images" in response.json()["detail"].lower()

    def test_load_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ):
        """Regular (non-admin) users should be rejected."""
        response = test_client.post(
            "/images/load",
            files={"file": ("img.tar", io.BytesIO(b"x"), "application/x-tar")},
            headers=auth_headers,
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# TestLoadImageBackground — POST /images/load?background=true
# ---------------------------------------------------------------------------


class TestLoadImageBackground:
    """Tests for POST /images/load?background=true."""

    def test_background_returns_upload_id(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Background mode should return an upload_id immediately."""
        from app.routers import images as img

        from app.services.resource_monitor import PressureLevel
        monkeypatch.setattr(
            "app.routers.images.ResourceMonitor.check_disk_pressure",
            staticmethod(lambda: PressureLevel.NORMAL),
        )

        # Stub out the background processing so nothing actually runs
        monkeypatch.setattr(img.threading, "Thread", lambda **kw: MagicMock(start=lambda: None))

        response = test_client.post(
            "/images/load",
            params={"background": "true"},
            files={"file": ("img.tar", io.BytesIO(b"data"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "upload_id" in data
        assert data["status"] == "started"

    def test_background_progress_tracking(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        auth_headers: dict,
        monkeypatch,
    ):
        """After a background upload starts, progress should be retrievable."""
        from app.routers import images as img

        from app.services.resource_monitor import PressureLevel
        monkeypatch.setattr(
            "app.routers.images.ResourceMonitor.check_disk_pressure",
            staticmethod(lambda: PressureLevel.NORMAL),
        )

        captured_upload_id = {}

        def fake_thread_start(self_thread):
            # Simulate progress update from the "background" thread
            upload_id = captured_upload_id["id"]
            img._update_progress(upload_id, "loading", "Loading...", 50)

        class FakeThread:
            def __init__(self, **kw):
                self._kw = kw

            def start(self):
                # Extract upload_id from the thread args
                args = self._kw.get("args", ())
                if args:
                    captured_upload_id["id"] = args[0]
                    fake_thread_start(self)

        monkeypatch.setattr(img.threading, "Thread", lambda **kw: FakeThread(**kw))

        response = test_client.post(
            "/images/load",
            params={"background": "true"},
            files={"file": ("img.tar", io.BytesIO(b"data"), "application/x-tar")},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        upload_id = response.json()["upload_id"]

        progress_resp = test_client.get(
            f"/images/load/{upload_id}/progress",
            headers=auth_headers,
        )
        assert progress_resp.status_code == 200
        pdata = progress_resp.json()
        assert pdata["phase"] == "loading"
        assert pdata["percent"] == 50

        # Clean up
        img._clear_progress(upload_id)


# ---------------------------------------------------------------------------
# TestUploadQcow2 — POST /images/qcow2
# ---------------------------------------------------------------------------


class TestUploadQcow2:
    """Tests for POST /images/qcow2."""

    def _patch_disk(self, monkeypatch, *, critical=False):
        from app.services.resource_monitor import PressureLevel

        level = PressureLevel.CRITICAL if critical else PressureLevel.NORMAL
        monkeypatch.setattr(
            "app.routers.images.ResourceMonitor.check_disk_pressure",
            staticmethod(lambda: level),
        )

    def test_successful_qcow2_upload(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """A valid qcow2 upload should succeed."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "qcow2_path", lambda fn: tmp_path / fn)
        monkeypatch.setattr(
            img,
            "_finalize_qcow2_upload",
            lambda p, *, auto_build=True: {
                "path": str(p),
                "filename": p.name,
            },
        )

        response = test_client.post(
            "/images/qcow2",
            files={
                "file": (
                    "veos-4.29.qcow2",
                    io.BytesIO(b"\x00" * 32),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "veos-4.29.qcow2"

    def test_missing_filename(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Upload without a filename should return 400."""
        self._patch_disk(monkeypatch)

        # FastAPI uses the filename from the multipart form; sending empty string
        response = test_client.post(
            "/images/qcow2",
            files={
                "file": (
                    "",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 400

    def test_wrong_extension(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Non-qcow2 extension should be rejected."""
        self._patch_disk(monkeypatch)

        response = test_client.post(
            "/images/qcow2",
            files={
                "file": (
                    "image.vmdk",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "qcow2" in response.json()["detail"].lower()

    def test_disk_pressure_blocks(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Critical disk pressure should reject qcow2 upload."""
        self._patch_disk(monkeypatch, critical=True)

        response = test_client.post(
            "/images/qcow2",
            files={
                "file": (
                    "img.qcow2",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 507

    def test_auto_build_false(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """auto_build=false should be forwarded to _finalize_qcow2_upload."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "qcow2_path", lambda fn: tmp_path / fn)

        captured: dict = {}

        def fake_finalize(p, *, auto_build=True):
            captured["auto_build"] = auto_build
            return {"path": str(p), "filename": p.name}

        monkeypatch.setattr(img, "_finalize_qcow2_upload", fake_finalize)

        response = test_client.post(
            "/images/qcow2",
            params={"auto_build": "false"},
            files={
                "file": (
                    "img.qcow2",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        assert captured["auto_build"] is False

    def test_qcow2_upload_requires_admin(
        self,
        test_client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ):
        """Regular users should be rejected."""
        response = test_client.post(
            "/images/qcow2",
            files={
                "file": (
                    "img.qcow2",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=auth_headers,
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# TestUploadIOL — POST /images/iol
# ---------------------------------------------------------------------------


class TestUploadIOL:
    """Tests for POST /images/iol."""

    def _patch_disk(self, monkeypatch, *, critical=False):
        from app.services.resource_monitor import PressureLevel

        level = PressureLevel.CRITICAL if critical else PressureLevel.NORMAL
        monkeypatch.setattr(
            "app.routers.images.ResourceMonitor.check_disk_pressure",
            staticmethod(lambda: level),
        )

    def test_l3_iol_detection(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """L3 IOL filename should be detected correctly."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "detect_iol_device_type", lambda fn: "iol-xe")
        monkeypatch.setattr(img, "iol_path", lambda fn: tmp_path / fn)

        manifest = _make_manifest()
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)
        monkeypatch.setattr(
            img, "detect_device_from_filename", lambda _: (None, "15.9")
        )
        monkeypatch.setattr(
            img,
            "create_image_entry",
            lambda **kw: {"id": kw["image_id"], **kw},
        )
        monkeypatch.setattr(
            img,
            "_enqueue_iol_build_job",
            lambda m, e: {"build_job_id": "rq-123", "build_status": "queued"},
        )

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "i86bi-linux-l3-15.9.bin",
                    io.BytesIO(b"\x7fELF"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "iol-xe"
        assert data["build_job_id"] == "rq-123"

    def test_l2_iol_detection(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """L2 IOL filename should be detected as iol-l2."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "detect_iol_device_type", lambda fn: "iol-l2")
        monkeypatch.setattr(img, "iol_path", lambda fn: tmp_path / fn)

        manifest = _make_manifest()
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)
        monkeypatch.setattr(
            img, "detect_device_from_filename", lambda _: (None, None)
        )
        monkeypatch.setattr(
            img,
            "create_image_entry",
            lambda **kw: {"id": kw["image_id"], **kw},
        )
        monkeypatch.setattr(
            img,
            "_enqueue_iol_build_job",
            lambda m, e: {"build_job_id": "rq-456", "build_status": "queued"},
        )

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "i86bi-linux-l2-15.2.bin",
                    io.BytesIO(b"\x7fELF"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["device_id"] == "iol-l2"

    def test_unrecognized_filename(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Unrecognized IOL filename should return 400."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "detect_iol_device_type", lambda fn: None)

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "random-binary.bin",
                    io.BytesIO(b"\x7fELF"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "detect" in response.json()["detail"].lower()

    def test_missing_filename(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Upload without a filename should return 400."""
        self._patch_disk(monkeypatch)

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 400

    def test_duplicate_iol(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Uploading a duplicate IOL binary should return 409."""
        from app.routers import images as img

        self._patch_disk(monkeypatch)
        monkeypatch.setattr(img, "detect_iol_device_type", lambda fn: "iol-xe")
        monkeypatch.setattr(img, "iol_path", lambda fn: tmp_path / fn)

        existing = {"id": "iol:i86bi-linux-l3-15.9.bin", "kind": "iol"}
        manifest = _make_manifest([existing])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "i86bi-linux-l3-15.9.bin",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 409

    def test_disk_pressure_blocks_iol(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Critical disk pressure should reject IOL upload."""
        self._patch_disk(monkeypatch, critical=True)

        response = test_client.post(
            "/images/iol",
            files={
                "file": (
                    "i86bi-linux-l3-15.9.bin",
                    io.BytesIO(b"x"),
                    "application/octet-stream",
                )
            },
            headers=admin_auth_headers,
        )
        assert response.status_code == 507


# ---------------------------------------------------------------------------
# TestConfirmQcow2Upload — POST /images/upload/{id}/confirm
# ---------------------------------------------------------------------------


class TestConfirmQcow2Upload:
    """Tests for POST /images/upload/{id}/confirm."""

    def _stage_session(self, monkeypatch, upload_id, *, status="awaiting_confirmation", final_path=None):
        """Put a fake session into _chunk_upload_sessions."""
        from app.routers import images as img

        session = {
            "upload_id": upload_id,
            "kind": "qcow2",
            "filename": "test.qcow2",
            "total_size": 100,
            "status": status,
            "final_path": str(final_path) if final_path else "/tmp/nonexistent.qcow2",
            "detection": {
                "detected_device_id": "eos",
                "detected_version": "4.29",
                "sha256": "abc123",
                "size_bytes": 100,
            },
        }
        img._chunk_upload_sessions[upload_id] = session
        return session

    def test_upload_id_not_found(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
    ):
        """Non-existent upload ID should return 404."""
        response = test_client.post(
            "/images/upload/nonexistent/confirm",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 404

    def test_wrong_status(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Session not in awaiting_confirmation should return 400."""
        from app.routers import images as img

        upload_id = "test-wrong-status"
        self._stage_session(monkeypatch, upload_id, status="uploading")

        response = test_client.post(
            f"/images/upload/{upload_id}/confirm",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "awaiting_confirmation" in response.json()["detail"]

        img._chunk_upload_sessions.pop(upload_id, None)

    def test_file_gone_from_disk(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """If the staged file is gone, should return 410."""
        from app.routers import images as img

        upload_id = "test-file-gone"
        self._stage_session(
            monkeypatch, upload_id, final_path="/tmp/does-not-exist-12345.qcow2"
        )

        response = test_client.post(
            f"/images/upload/{upload_id}/confirm",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 410
        assert "no longer exists" in response.json()["detail"].lower()

        img._chunk_upload_sessions.pop(upload_id, None)

    def test_override_device_id_version(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """User-provided device_id/version should override detection."""
        from app.routers import images as img

        upload_id = "test-override"
        fp = tmp_path / "test.qcow2"
        fp.write_bytes(b"\x00" * 64)
        self._stage_session(monkeypatch, upload_id, final_path=fp)

        captured: dict = {}

        def fake_register(dest, *, device_id=None, version=None, sha256=None,
                          size_bytes=None, auto_build=True, metadata=None):
            captured["device_id"] = device_id
            captured["version"] = version
            return {"path": str(dest), "filename": dest.name}

        monkeypatch.setattr(img, "_register_qcow2", fake_register)

        response = test_client.post(
            f"/images/upload/{upload_id}/confirm",
            json={"device_id": "xrv9k", "version": "7.8.1"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        assert captured["device_id"] == "xrv9k"
        assert captured["version"] == "7.8.1"

        img._chunk_upload_sessions.pop(upload_id, None)

    def test_successful_confirmation(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Successful confirmation should return completed status."""
        from app.routers import images as img

        upload_id = "test-success-confirm"
        fp = tmp_path / "test.qcow2"
        fp.write_bytes(b"\x00" * 64)
        self._stage_session(monkeypatch, upload_id, final_path=fp)

        monkeypatch.setattr(
            img,
            "_register_qcow2",
            lambda dest, **kw: {"path": str(dest), "filename": dest.name},
        )

        response = test_client.post(
            f"/images/upload/{upload_id}/confirm",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["kind"] == "qcow2"

        img._chunk_upload_sessions.pop(upload_id, None)


# ---------------------------------------------------------------------------
# TestTriggerDockerBuild — POST /images/library/{id}/build-docker
# ---------------------------------------------------------------------------


class TestTriggerDockerBuild:
    """Tests for POST /images/library/{id}/build-docker."""

    def test_image_not_found(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Missing image should return 404."""
        from app.routers import images as img

        monkeypatch.setattr(img, "load_manifest", lambda: _make_manifest())
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)

        response = test_client.post(
            "/images/library/nonexistent/build-docker",
            headers=admin_auth_headers,
        )
        assert response.status_code == 404

    def test_non_qcow2_rejected(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Non-qcow2 image should return 400."""
        from app.routers import images as img

        docker_img = _docker_image()
        manifest = _make_manifest([docker_img])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.post(
            "/images/library/docker%3Aceos%3A4.28.0/build-docker",
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "qcow2" in response.json()["detail"].lower()

    def test_file_missing_from_disk(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """qcow2 file not on disk should return 400."""
        from app.routers import images as img

        qcow2 = {
            "id": "qcow2:missing.qcow2",
            "kind": "qcow2",
            "reference": "/nonexistent/missing.qcow2",
            "filename": "missing.qcow2",
        }
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.post(
            "/images/library/qcow2%3Amissing.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_unrecognized_device_type(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Unrecognized device type should return 400."""
        from app.routers import images as img

        fp = tmp_path / "unknown.qcow2"
        fp.write_bytes(b"\x00" * 32)

        qcow2 = {
            "id": "qcow2:unknown.qcow2",
            "kind": "qcow2",
            "reference": str(fp),
            "filename": "unknown.qcow2",
        }
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )
        monkeypatch.setattr(
            img, "detect_qcow2_device_type", lambda fn: (None, None)
        )

        response = test_client.post(
            "/images/library/qcow2%3Aunknown.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "not recognized" in response.json()["detail"].lower()

    def test_successful_build_trigger(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Valid qcow2 should queue a build job."""
        from app.routers import images as img

        fp = tmp_path / "veos-4.29.qcow2"
        fp.write_bytes(b"\x00" * 32)

        qcow2 = {
            "id": "qcow2:veos-4.29.qcow2",
            "kind": "qcow2",
            "reference": str(fp),
            "filename": "veos-4.29.qcow2",
            "device_id": "eos",
            "version": "4.29",
        }
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )
        monkeypatch.setattr(
            img, "detect_qcow2_device_type", lambda fn: ("eos", "vr-veos")
        )

        queued_job = MagicMock()
        queued_job.id = "rq-build-789"
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = queued_job
        monkeypatch.setattr(img, "get_queue", lambda: mock_queue)

        response = test_client.post(
            "/images/library/qcow2%3Aveos-4.29.qcow2/build-docker",
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "rq-build-789"
        assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# TestBackfillChecksums — POST /images/backfill-checksums
# ---------------------------------------------------------------------------


class TestBackfillChecksums:
    """Tests for POST /images/backfill-checksums."""

    def test_empty_manifest(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Empty manifest should produce zero updates."""
        from app.routers import images as img

        monkeypatch.setattr(img, "load_manifest", lambda: _make_manifest())
        monkeypatch.setattr(img, "save_manifest", lambda m: None)

        response = test_client.post(
            "/images/backfill-checksums", headers=admin_auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 0
        assert data["errors"] == []

    def test_skips_existing_checksums(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Images that already have sha256 should be skipped."""
        from app.routers import images as img

        fp = tmp_path / "img.qcow2"
        fp.write_bytes(b"\x00" * 16)
        qcow2 = {
            "id": "qcow2:img.qcow2",
            "kind": "qcow2",
            "reference": str(fp),
            "sha256": "already_hashed",
        }
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)

        response = test_client.post(
            "/images/backfill-checksums", headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 0

    def test_skips_non_qcow2_images(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Docker images should be skipped (only qcow2 processed)."""
        from app.routers import images as img

        docker = _docker_image()
        docker.pop("sha256", None)  # Ensure no sha256
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)

        response = test_client.post(
            "/images/backfill-checksums", headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert response.json()["updated"] == 0

    def test_file_missing_from_disk(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Missing qcow2 file should be recorded as an error."""
        from app.routers import images as img

        qcow2 = {
            "id": "qcow2:gone.qcow2",
            "kind": "qcow2",
            "reference": "/nonexistent/gone.qcow2",
        }
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: None)

        response = test_client.post(
            "/images/backfill-checksums", headers=admin_auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 0
        assert len(data["errors"]) == 1
        assert "not found" in data["errors"][0].lower()

    def test_successful_checksum_computation(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Valid qcow2 without sha256 should get a computed checksum."""
        from app.routers import images as img

        fp = tmp_path / "needscheck.qcow2"
        fp.write_bytes(b"\x00" * 16)
        qcow2 = {
            "id": "qcow2:needscheck.qcow2",
            "kind": "qcow2",
            "reference": str(fp),
        }
        manifest = _make_manifest([qcow2])
        saved_manifests: list[dict] = []
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(img, "save_manifest", lambda m: saved_manifests.append(m))

        monkeypatch.setattr(
            "app.routers.images.compute_sha256",
            lambda path: "deadbeef1234",
        )

        response = test_client.post(
            "/images/backfill-checksums", headers=admin_auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 1
        assert qcow2["sha256"] == "deadbeef1234"


# ---------------------------------------------------------------------------
# TestPushImageToHosts — POST /images/library/{id}/push
# ---------------------------------------------------------------------------


class TestPushImageToHosts:
    """Tests for POST /images/library/{id}/push."""

    def test_image_not_found(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """Non-existent image should return 404."""
        from app.routers import images as img

        monkeypatch.setattr(img, "load_manifest", lambda: _make_manifest())
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)

        response = test_client.post(
            "/images/library/nonexistent/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 404

    def test_no_online_hosts(
        self,
        test_client: TestClient,
        admin_user: models.User,
        admin_auth_headers: dict,
        monkeypatch,
    ):
        """When no online hosts exist, should return 400."""
        from app.routers import images as img

        docker = _docker_image()
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.post(
            "/images/library/docker%3Aceos%3A4.28.0/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 400
        assert "no online hosts" in response.json()["detail"].lower()

    def test_creates_sync_jobs(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
        sample_host: models.Host,
        monkeypatch,
    ):
        """Push should create sync jobs for online hosts."""
        from app.routers import images as img

        docker = _docker_image()
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        # Stub out the async background task so it does not actually run
        monkeypatch.setattr(img.asyncio, "create_task", lambda coro: coro.close())

        response = test_client.post(
            "/images/library/docker%3Aceos%3A4.28.0/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        assert len(data["jobs"]) >= 1

    def test_deduplication_existing_job(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
        sample_host: models.Host,
        monkeypatch,
    ):
        """Duplicate push should reuse existing pending job."""
        from app.routers import images as img

        docker = _docker_image()
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )
        monkeypatch.setattr(img.asyncio, "create_task", lambda coro: coro.close())

        # Pre-create a pending sync job for this image/host
        existing_job = models.ImageSyncJob(
            id="existing-sync-job",
            image_id=docker["id"],
            host_id=sample_host.id,
            status="pending",
        )
        test_db.add(existing_job)
        test_db.commit()

        response = test_client.post(
            "/images/library/docker%3Aceos%3A4.28.0/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # Should reuse the existing job rather than creating a new one
        assert "existing-sync-job" in data["jobs"]

    def test_concurrency_limit(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        admin_auth_headers: dict,
        sample_host: models.Host,
        monkeypatch,
    ):
        """Host at concurrency limit should be skipped."""
        from app.routers import images as img
        from app.config import settings

        docker = _docker_image()
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )
        monkeypatch.setattr(img.asyncio, "create_task", lambda coro: coro.close())

        # Fill up the concurrency slots with active jobs for different images
        max_concurrent = settings.image_sync_max_concurrent
        for i in range(max_concurrent):
            job = models.ImageSyncJob(
                id=f"active-job-{i}",
                image_id=f"docker:other{i}:1.0",
                host_id=sample_host.id,
                status="transferring",
            )
            test_db.add(job)
        test_db.commit()

        response = test_client.post(
            "/images/library/docker%3Aceos%3A4.28.0/push",
            json={},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # Host should be skipped due to concurrency limit
        assert data["count"] == 0


# ---------------------------------------------------------------------------
# TestStreamImage — GET /images/library/{id}/stream
# ---------------------------------------------------------------------------


class TestStreamImage:
    """Tests for GET /images/library/{id}/stream."""

    def test_image_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ):
        """Non-existent image should return 404."""
        from app.routers import images as img

        monkeypatch.setattr(img, "load_manifest", lambda: _make_manifest())
        monkeypatch.setattr(img, "find_image_by_id", lambda m, _id: None)

        response = test_client.get(
            "/images/library/nonexistent/stream", headers=auth_headers
        )
        assert response.status_code == 404

    def test_non_docker_image(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Streaming a non-docker image should return 400."""
        from app.routers import images as img

        qcow2 = _qcow2_image(tmp_path)
        manifest = _make_manifest([qcow2])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.get(
            "/images/library/qcow2%3Aveos-4.29.qcow2/stream",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "docker" in response.json()["detail"].lower()

    def test_no_reference(
        self,
        test_client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ):
        """Docker image with empty reference should return 400."""
        from app.routers import images as img

        docker = {
            "id": "docker:noref:1.0",
            "kind": "docker",
            "reference": "",
        }
        manifest = _make_manifest([docker])
        monkeypatch.setattr(img, "load_manifest", lambda: manifest)
        monkeypatch.setattr(
            img,
            "find_image_by_id",
            lambda m, _id: next(
                (i for i in m["images"] if i["id"] == _id), None
            ),
        )

        response = test_client.get(
            "/images/library/docker%3Anoref%3A1.0/stream",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "reference" in response.json()["detail"].lower()
