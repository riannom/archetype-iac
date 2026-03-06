"""Tests for api/app/routers/iso.py — round 12.

Targets deep paths in _import_single_image (qcow2/docker/iol/unknown branches),
SSE streaming, _execute_import edge cases, _update_image_progress, and
endpoint-level error handling.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.iso.models import (
    ISOFormat,
    ISOManifest,
    ISOSession,
    ImageImportProgress,
    ParsedImage,
    ParsedNodeDefinition,
)
from app.routers import iso as iso_mod
from app.routers.iso import (
    _execute_import,
    _import_single_image,
    _save_session,
    _get_session,
    _delete_session,
    _sessions,
    _session_lock,
    _sse_event,
    _update_image_progress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine, handling both running and non-running event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


def _make_parsed_image(
    image_id="img1",
    image_type="qcow2",
    disk_image_path="images/disk.qcow2",
    disk_image_filename="disk.qcow2",
    node_definition_id="nd1",
    version="1.0",
    **kwargs,
) -> ParsedImage:
    return ParsedImage(
        id=image_id,
        image_type=image_type,
        disk_image_path=disk_image_path,
        disk_image_filename=disk_image_filename,
        node_definition_id=node_definition_id,
        version=version,
        **kwargs,
    )


def _make_node_def(
    node_def_id="nd1",
    ram_mb=4096,
    cpus=2,
    boot_completed_patterns=None,
    **kwargs,
) -> ParsedNodeDefinition:
    return ParsedNodeDefinition(
        id=node_def_id,
        label="Test Device",
        ram_mb=ram_mb,
        cpus=cpus,
        boot_completed_patterns=boot_completed_patterns or [],
        **kwargs,
    )


def _make_real_session(session_id, **overrides):
    """Create a real ISOSession (not a MagicMock)."""
    defaults = dict(
        id=session_id,
        iso_path="/tmp/test.iso",
        status="importing",
    )
    defaults.update(overrides)
    return ISOSession(**defaults)


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Ensure session state is clean between tests."""
    with _session_lock:
        _sessions.clear()
    yield
    with _session_lock:
        _sessions.clear()


# ===========================================================================
# _sse_event formatting
# ===========================================================================


class TestSSEEvent:

    def test_sse_event_format(self):
        result = _sse_event("progress", {"status": "importing", "percent": 42})
        assert result.startswith("event: progress\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        data_line = result.split("data: ", 1)[1].rstrip("\n")
        parsed = json.loads(data_line)
        assert parsed["status"] == "importing"
        assert parsed["percent"] == 42

    def test_sse_event_error_type(self):
        result = _sse_event("error", {"message": "Session not found"})
        assert "event: error\n" in result
        parsed = json.loads(result.split("data: ", 1)[1].rstrip("\n"))
        assert parsed["message"] == "Session not found"

    def test_sse_event_complete_type(self):
        result = _sse_event("complete", {"status": "completed", "error_message": None})
        assert "event: complete\n" in result


# ===========================================================================
# _update_image_progress
# ===========================================================================


class TestUpdateImageProgress:

    def test_update_creates_entry_for_missing_image(self):
        sess = _make_real_session("s1", selected_images=["img1"])
        _save_session(sess)
        _update_image_progress("s1", "img1", "extracting", 10)
        updated = _get_session("s1")
        assert "img1" in updated.image_progress
        prog = updated.image_progress["img1"]
        assert prog["status"] == "extracting"
        assert prog["progress_percent"] == 10

    def test_update_sets_started_at_on_extracting(self):
        sess = _make_real_session("s1")
        _save_session(sess)
        _update_image_progress("s1", "img1", "extracting", 5)
        updated = _get_session("s1")
        assert "started_at" in updated.image_progress["img1"]

    def test_update_sets_completed_at_on_completed(self):
        sess = _make_real_session("s1")
        _save_session(sess)
        _update_image_progress("s1", "img1", "completed", 100)
        updated = _get_session("s1")
        assert "completed_at" in updated.image_progress["img1"]

    def test_update_sets_completed_at_on_failed(self):
        sess = _make_real_session("s1")
        _save_session(sess)
        _update_image_progress("s1", "img1", "failed", 0, "disk error")
        updated = _get_session("s1")
        prog = updated.image_progress["img1"]
        assert prog["error_message"] == "disk error"
        assert "completed_at" in prog

    def test_update_noop_when_session_gone(self):
        """No error when session doesn't exist."""
        _update_image_progress("nonexistent", "img1", "extracting", 5)
        # Should not raise

    def test_update_does_not_overwrite_started_at(self):
        sess = _make_real_session("s1")
        _save_session(sess)
        _update_image_progress("s1", "img1", "extracting", 5)
        first_started = _get_session("s1").image_progress["img1"]["started_at"]
        _update_image_progress("s1", "img1", "extracting", 50)
        second_started = _get_session("s1").image_progress["img1"]["started_at"]
        assert first_started == second_started


# ===========================================================================
# _import_single_image — qcow2 path
# ===========================================================================


class TestImportSingleImageQcow2:

    def test_qcow2_extracts_and_creates_entry(self):
        image = _make_parsed_image()
        node_def = _make_node_def()
        store = Path("/tmp/fake-store")
        manifest_data = {"images": []}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "qcow2:disk.qcow2", "compatible_devices": ["dev1"],
             }) as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 1024000
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            _run(_import_single_image(
                "s1", image, [node_def], extractor, store, manifest_data,
                create_devices=False, iso_source="test.iso",
            ))

            extractor.extract_file.assert_awaited_once()
            mock_create.assert_called_once()
            assert len(manifest_data["images"]) == 1

    def test_qcow2_reuses_existing_file(self):
        """When dest_path already exists, skip extraction."""
        image = _make_parsed_image()
        node_def = _make_node_def()
        manifest_data = {"images": []}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "qcow2:disk.qcow2", "compatible_devices": ["dev1"],
             }), \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 1024000
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            _run(_import_single_image(
                "s1", image, [node_def], extractor, Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            # Should NOT extract since file exists
            extractor.extract_file.assert_not_awaited()

    def test_qcow2_shared_filename_sets_compat_devices(self):
        """Multiple devices sharing same qcow2 file get merged into compatible_devices."""
        image = _make_parsed_image()
        manifest_data = {"images": []}
        filename_to_devices = {"disk.qcow2": ["dev1", "dev2"]}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999
            mock_create.return_value = {"id": "qcow2:disk.qcow2", "compatible_devices": ["dev1", "dev2"]}

            _run(_import_single_image(
                "s1", image, [], MagicMock(), Path("/tmp/store"),
                manifest_data, create_devices=False,
                filename_to_devices=filename_to_devices,
            ))

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["compatible_devices"] == ["dev1", "dev2"]

    def test_qcow2_vendor_probe_none_clears_readiness(self):
        """When vendor config has readiness_probe='none', don't set readiness fields."""
        image = _make_parsed_image()
        node_def = _make_node_def(boot_completed_patterns=["login:"])
        manifest_data = {"images": []}

        vendor_cfg = MagicMock()
        vendor_cfg.readiness_probe = "none"

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=vendor_cfg), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999
            mock_create.return_value = {"id": "qcow2:disk.qcow2", "compatible_devices": ["dev1"]}

            _run(_import_single_image(
                "s1", image, [node_def], MagicMock(), Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["readiness_probe"] is None
            assert call_kwargs["readiness_pattern"] is None

    def test_qcow2_boot_patterns_set_log_pattern(self):
        """When node def has boot_completed_patterns, readiness probe is log_pattern."""
        image = _make_parsed_image()
        node_def = _make_node_def(boot_completed_patterns=["login:", "Password:"])
        manifest_data = {"images": []}

        vendor_cfg = MagicMock()
        vendor_cfg.readiness_probe = "log_pattern"

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=vendor_cfg), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999
            mock_create.return_value = {"id": "test", "compatible_devices": ["dev1"]}

            _run(_import_single_image(
                "s1", image, [node_def], MagicMock(), Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["readiness_probe"] == "log_pattern"
            assert call_kwargs["readiness_pattern"] == "login:|Password:"


# ===========================================================================
# _import_single_image — docker path
# ===========================================================================


class TestImportSingleImageDocker:

    def test_docker_extract_load_and_register(self):
        """Full docker import path: extract tar, docker load, parse ref, create entry."""
        image = _make_parsed_image(
            image_type="docker",
            disk_image_filename="ceos.tar.gz",
            disk_image_path="images/ceos.tar.gz",
        )
        manifest_data = {"images": []}

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(
            b"Loaded image: ceos:4.28.0F\n", b""
        ))

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("ceos", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait, \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 500000
            mock_wait.return_value = (b"Loaded image: ceos:4.28.0F\n", b"")
            mock_create.return_value = {"id": "docker:ceos:4.28.0F", "compatible_devices": ["ceos"]}
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            _run(_import_single_image(
                "s1", image, [], extractor, Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            extractor.extract_file.assert_awaited_once()
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["kind"] == "docker"
            assert call_kwargs["reference"] == "ceos:4.28.0F"

    def test_docker_load_failure_raises(self):
        """docker load returning non-zero raises RuntimeError."""
        image = _make_parsed_image(
            image_type="docker",
            disk_image_filename="bad.tar.gz",
            disk_image_path="images/bad.tar.gz",
        )
        manifest_data = {"images": []}

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error loading"))

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_custom_device"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (b"", b"Error loading")
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            with pytest.raises(RuntimeError, match="docker load failed"):
                _run(_import_single_image(
                    "s1", image, [], extractor, Path("/tmp/store"),
                    manifest_data, create_devices=False,
                ))

    def test_docker_no_loaded_image_ref_raises(self):
        """When docker load output has no 'Loaded image:' line, raise RuntimeError."""
        image = _make_parsed_image(
            image_type="docker",
            disk_image_filename="weird.tar.gz",
            disk_image_path="images/weird.tar.gz",
        )
        manifest_data = {"images": []}

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Some other output\n", b""))

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_custom_device"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = (b"Some other output\n", b"")
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            with pytest.raises(RuntimeError, match="Could not determine loaded image"):
                _run(_import_single_image(
                    "s1", image, [], extractor, Path("/tmp/store"),
                    manifest_data, create_devices=False,
                ))

    def test_docker_loaded_image_id_fallback(self):
        """'Loaded image ID:' line is also accepted as reference."""
        image = _make_parsed_image(
            image_type="docker",
            disk_image_filename="img.tar.gz",
            disk_image_path="images/img.tar.gz",
        )
        manifest_data = {"images": []}

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(
            b"Loaded image ID: sha256:abc123\n", b""
        ))

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", new_callable=AsyncMock) as mock_wait, \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 100
            mock_wait.return_value = (b"Loaded image ID: sha256:abc123\n", b"")
            mock_create.return_value = {"id": "docker:sha256:abc123", "compatible_devices": ["dev1"]}

            _run(_import_single_image(
                "s1", image, [], MagicMock(extract_file=AsyncMock()),
                Path("/tmp/store"), manifest_data, create_devices=False,
            ))

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["reference"] == "sha256:abc123"


# ===========================================================================
# _import_single_image — IOL path
# ===========================================================================


class TestImportSingleImageIOL:

    def test_iol_extract_and_enqueue_build(self):
        """IOL image extraction + RQ build job enqueue."""
        image = _make_parsed_image(
            image_type="iol",
            disk_image_filename="iol.bin",
            disk_image_path="images/iol.bin",
        )
        manifest_data = {"images": []}

        mock_job = MagicMock()
        mock_job.id = "build-job-123"
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_job

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("iol", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("app.jobs.get_queue", return_value=mock_queue), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 50000
            entry = {
                "id": "iol:img1",
                "compatible_devices": ["iol"],
            }
            mock_create.return_value = entry
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            _run(_import_single_image(
                "s1", image, [], extractor, Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            extractor.extract_file.assert_awaited_once()
            mock_queue.enqueue.assert_called_once()
            assert entry["build_status"] == "queued"
            assert entry["build_job_id"] == "build-job-123"

    def test_iol_reuses_existing_file(self):
        """IOL file already on disk skips extraction."""
        image = _make_parsed_image(
            image_type="iol",
            disk_image_filename="iol.bin",
            disk_image_path="images/iol.bin",
        )
        manifest_data = {"images": []}

        mock_job = MagicMock(id="j1")
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_job

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("iol", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "iol:img1", "compatible_devices": ["iol"],
             }), \
             patch("app.routers.iso.find_custom_device"), \
             patch("app.jobs.get_queue", return_value=mock_queue), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 50000
            extractor = MagicMock()
            extractor.extract_file = AsyncMock()

            _run(_import_single_image(
                "s1", image, [], extractor, Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            extractor.extract_file.assert_not_awaited()


# ===========================================================================
# _import_single_image — validation errors
# ===========================================================================


class TestImportSingleImageValidation:

    def test_no_disk_image_path_raises_value_error(self):
        image = _make_parsed_image(disk_image_path="")
        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            with pytest.raises(ValueError, match="No disk image path"):
                _run(_import_single_image(
                    "s1", image, [], MagicMock(), Path("/tmp"),
                    {"images": []}, False,
                ))

    def test_unknown_image_type_raises(self):
        image = _make_parsed_image(image_type="unknown", disk_image_path="images/x.bin")
        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            with pytest.raises(ValueError, match="Unknown image type"):
                _run(_import_single_image(
                    "s1", image, [], MagicMock(), Path("/tmp"),
                    {"images": []}, False,
                ))

    def test_unsupported_image_type_raises(self):
        image = _make_parsed_image(image_type="vmdk", disk_image_path="images/x.vmdk")
        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            with pytest.raises(ValueError, match="Unsupported image type"):
                _run(_import_single_image(
                    "s1", image, [], MagicMock(), Path("/tmp"),
                    {"images": []}, False,
                ))

    def test_custom_device_skipped_when_existing(self):
        """If custom device already exists, add_custom_device is not called."""
        image = _make_parsed_image()
        new_dev = {"id": "custom-dev", "name": "Custom"}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("custom-dev", new_dev)), \
             patch("app.routers.iso.find_custom_device", return_value={"id": "custom-dev"}) as mock_find, \
             patch("app.routers.iso.add_custom_device") as mock_add, \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "test", "compatible_devices": ["custom-dev"],
             }), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999

            _run(_import_single_image(
                "s1", image, [], MagicMock(), Path("/tmp/store"),
                {"images": []}, create_devices=True,
            ))

            mock_find.assert_called_once_with("custom-dev")
            mock_add.assert_not_called()

    def test_custom_device_not_created_when_flag_off(self):
        """When create_devices=False, never call add_custom_device."""
        image = _make_parsed_image()
        new_dev = {"id": "new-dev", "name": "New"}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("new-dev", new_dev)), \
             patch("app.routers.iso.find_custom_device") as mock_find, \
             patch("app.routers.iso.add_custom_device") as mock_add, \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "test", "compatible_devices": ["new-dev"],
             }), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999

            _run(_import_single_image(
                "s1", image, [], MagicMock(), Path("/tmp/store"),
                {"images": []}, create_devices=False,
            ))

            mock_find.assert_not_called()
            mock_add.assert_not_called()


# ===========================================================================
# _import_single_image — duplicate handling
# ===========================================================================


class TestImportSingleImageDuplicates:

    def test_duplicate_merges_compatible_devices(self):
        """When image already exists in manifest, merge compatible_devices."""
        image = _make_parsed_image()
        existing_entry = {
            "id": "qcow2:disk.qcow2",
            "compatible_devices": ["dev1"],
        }
        manifest_data = {"images": [existing_entry]}

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev2", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=existing_entry), \
             patch("app.routers.iso.create_image_entry", return_value={
                 "id": "qcow2:disk.qcow2", "compatible_devices": ["dev2"],
             }), \
             patch("app.routers.iso.find_custom_device"), \
             patch("agent.vendors.get_config_by_device", return_value=None), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 999

            _run(_import_single_image(
                "s1", image, [], MagicMock(), Path("/tmp/store"),
                manifest_data, create_devices=False,
            ))

            assert "dev2" in existing_entry["compatible_devices"]
            # Only the original entry, no duplicate appended
            assert len(manifest_data["images"]) == 1

    def test_duplicate_iol_merges_build_fields(self):
        """IOL duplicate merges build_status and build_job_id."""
        image = _make_parsed_image(
            image_type="iol",
            disk_image_filename="iol.bin",
            disk_image_path="images/iol.bin",
        )
        existing_entry = {
            "id": "iol:img1",
            "compatible_devices": ["iol"],
        }
        manifest_data = {"images": [existing_entry]}

        mock_job = MagicMock(id="job-456")
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = mock_job

        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("iol", None)), \
             patch("app.routers.iso.find_image_by_id", return_value=existing_entry), \
             patch("app.routers.iso.create_image_entry") as mock_create, \
             patch("app.routers.iso.find_custom_device"), \
             patch("app.jobs.get_queue", return_value=mock_queue), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 50000
            new_entry = {
                "id": "iol:img1",
                "compatible_devices": ["iol"],
            }
            mock_create.return_value = new_entry

            _run(_import_single_image(
                "s1", image, [], MagicMock(extract_file=AsyncMock()),
                Path("/tmp/store"), manifest_data, create_devices=False,
            ))

            assert existing_entry.get("build_status") == "queued"
            assert existing_entry.get("build_job_id") == "job-456"


# ===========================================================================
# _execute_import — additional edge cases
# ===========================================================================


class TestExecuteImportEdgeCases:

    def test_image_not_in_manifest_updates_failed(self):
        """Image ID not found in manifest.images triggers failed progress."""
        sess = _make_real_session("s1", selected_images=["nonexistent"])
        manifest = ISOManifest(
            iso_path="/tmp/test.iso",
            format=ISOFormat.VIRL2,
            images=[],
            node_definitions=[],
        )
        sess.manifest = manifest
        _save_session(sess)

        with patch("app.routers.iso.ISOExtractor") as mock_ext, \
             patch("app.routers.iso.ensure_image_store", return_value=Path("/tmp")), \
             patch("app.routers.iso.load_manifest", return_value={"images": []}), \
             patch("app.routers.iso.save_manifest"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            mock_ext.return_value.cleanup = MagicMock()
            _run(_execute_import("s1"))

        updated = _get_session("s1")
        assert updated.status == "completed"

    def test_session_disappears_mid_progress_update(self):
        """If session is deleted during progress update, import continues gracefully."""
        img = _make_parsed_image()
        manifest = ISOManifest(
            iso_path="/tmp/test.iso",
            format=ISOFormat.VIRL2,
            images=[img],
            node_definitions=[],
        )
        sess = _make_real_session("s2", selected_images=["img1"])
        sess.manifest = manifest
        _save_session(sess)

        call_count = 0
        original_get = _get_session

        def get_session_losing_session(sid):
            nonlocal call_count
            call_count += 1
            # After several calls, remove the session to simulate disappearance
            if call_count > 5:
                _delete_session(sid)
                return None
            return original_get(sid)

        with patch("app.routers.iso._get_session", side_effect=get_session_losing_session), \
             patch("app.routers.iso.ISOExtractor") as mock_ext, \
             patch("app.routers.iso.ensure_image_store", return_value=Path("/tmp")), \
             patch("app.routers.iso.load_manifest", return_value={"images": []}), \
             patch("app.routers.iso.save_manifest"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso._import_single_image", new_callable=AsyncMock), \
             patch("app.routers.iso._update_image_progress"):
            mock_ext.return_value.cleanup = MagicMock()
            _run(_execute_import("s2"))
        # Should not raise


# ===========================================================================
# Endpoint-level SSE stream tests
# ===========================================================================


class TestStreamEndpoint:

    def test_stream_404_missing_session(self, test_client, auth_headers):
        resp = test_client.get("/iso/nonexistent/stream", headers=auth_headers)
        assert resp.status_code == 404

    def test_stream_completed_session_emits_complete(self, test_client, auth_headers):
        """A completed session emits progress then complete events immediately."""
        sess = _make_real_session("stream-done", status="completed", progress_percent=100)
        _save_session(sess)

        resp = test_client.get("/iso/stream-done/stream", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        body = resp.text
        assert "event: progress" in body
        assert "event: complete" in body

    def test_stream_failed_session_emits_error(self, test_client, auth_headers):
        """A failed session emits progress then complete with error."""
        sess = _make_real_session(
            "stream-fail", status="failed", error_message="disk full",
        )
        _save_session(sess)

        resp = test_client.get("/iso/stream-fail/stream", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.text
        assert "event: complete" in body
        assert "disk full" in body


# ===========================================================================
# Endpoint-level delete session tests
# ===========================================================================


class TestDeleteSessionEndpoint:

    def test_delete_nonexistent_session_404(self, test_client, auth_headers):
        resp = test_client.delete("/iso/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_importing_session_cancels(self, test_client, auth_headers):
        sess = _make_real_session("del-importing", status="importing")
        _save_session(sess)

        resp = test_client.delete("/iso/del-importing", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "Session deleted"

    def test_delete_completed_session(self, test_client, auth_headers):
        sess = _make_real_session("del-done", status="completed")
        _save_session(sess)

        resp = test_client.delete("/iso/del-done", headers=auth_headers)
        assert resp.status_code == 200
        assert _get_session("del-done") is None


# ===========================================================================
# Endpoint-level get session info tests
# ===========================================================================


class TestGetSessionInfoEndpoint:

    def test_get_session_info_404(self, test_client, auth_headers):
        resp = test_client.get("/iso/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    def test_get_session_info_success(self, test_client, auth_headers):
        sess = _make_real_session("info-sess", status="scanned")
        _save_session(sess)

        resp = test_client.get("/iso/info-sess", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "info-sess"
        assert data["status"] == "scanned"
        assert data["manifest"] is None

    def test_get_session_info_with_manifest(self, test_client, auth_headers):
        manifest = ISOManifest(
            iso_path="/tmp/test.iso",
            format=ISOFormat.VIRL2,
            size_bytes=1000,
        )
        sess = _make_real_session("info-manifest", status="scanned")
        sess.manifest = manifest
        _save_session(sess)

        resp = test_client.get("/iso/info-manifest", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["manifest"] is not None
        assert data["manifest"]["format"] == "virl2"


# ===========================================================================
# Endpoint-level manifest endpoint
# ===========================================================================


class TestGetManifestEndpoint:

    def test_manifest_404_missing_session(self, test_client, auth_headers):
        resp = test_client.get("/iso/nonexistent/manifest", headers=auth_headers)
        assert resp.status_code == 404

    def test_manifest_400_not_scanned(self, test_client, auth_headers):
        sess = _make_real_session("no-manifest", status="pending")
        _save_session(sess)

        resp = test_client.get("/iso/no-manifest/manifest", headers=auth_headers)
        assert resp.status_code == 400

    def test_manifest_success(self, test_client, auth_headers):
        manifest = ISOManifest(
            iso_path="/tmp/test.iso",
            format=ISOFormat.VIRL2,
            size_bytes=5000,
            node_definitions=[_make_node_def()],
            images=[_make_parsed_image()],
        )
        sess = _make_real_session("has-manifest", status="scanned")
        sess.manifest = manifest
        _save_session(sess)

        resp = test_client.get("/iso/has-manifest/manifest", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "has-manifest"
        assert len(data["manifest"]["images"]) == 1
        assert len(data["manifest"]["node_definitions"]) == 1
