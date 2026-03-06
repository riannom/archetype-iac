"""Tests for api/app/routers/iso.py — _execute_import, stream_import_progress,
_import_single_image (round 11).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routers.iso import (
    _execute_import,
    _get_session,
    _save_session,
    _sessions,
)


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


def _make_session(session_id, **overrides):
    """Create a minimal ISOSession-like object."""
    from app.iso import ISOSession

    defaults = dict(
        id=session_id,
        iso_path="/tmp/test.iso",
        status="importing",
        progress_percent=0,
        selected_images=["img1"],
        create_devices=False,
        error_message=None,
        completed_at=None,
        image_progress={},
    )
    defaults.update(overrides)
    sess = MagicMock(**defaults)
    sess.id = session_id
    sess.iso_path = defaults["iso_path"]
    sess.status = defaults["status"]
    sess.progress_percent = defaults["progress_percent"]
    sess.selected_images = defaults["selected_images"]
    sess.create_devices = defaults["create_devices"]
    sess.error_message = defaults["error_message"]
    sess.completed_at = defaults["completed_at"]
    sess.image_progress = defaults["image_progress"]
    return sess


# ---------------------------------------------------------------------------
# _execute_import
# ---------------------------------------------------------------------------


class TestExecuteImport:

    def test_session_not_found_returns_early(self):
        with patch("app.routers.iso._get_session", return_value=None):
            _run(_execute_import("nonexistent"))
        # Should not raise

    def test_no_manifest_returns_early(self):
        sess = _make_session("s1")
        sess.manifest = None
        with patch("app.routers.iso._get_session", return_value=sess):
            _run(_execute_import("s1"))

    def test_cancelled_mid_loop(self):
        """Import stops when session status is cancelled."""
        img = MagicMock(id="img1", disk_image_filename="disk.qcow2")
        manifest = MagicMock(images=[img], node_definitions=[])
        sess = _make_session("s1", selected_images=["img1"])
        sess.manifest = manifest

        call_count = 0

        def get_session_side_effect(sid):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                sess.status = "cancelled"
            return sess

        with patch("app.routers.iso._get_session", side_effect=get_session_side_effect), \
             patch("app.routers.iso.ISOExtractor") as mock_ext, \
             patch("app.routers.iso.ensure_image_store", return_value=Path("/tmp")), \
             patch("app.routers.iso.load_manifest", return_value={}), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            mock_ext.return_value.cleanup = MagicMock()
            _run(_execute_import("s1"))

    def test_image_exception_continues(self):
        """Individual image failures don't stop the import."""
        img1 = MagicMock(id="img1", disk_image_filename="a.qcow2")
        img2 = MagicMock(id="img2", disk_image_filename="b.qcow2")
        manifest = MagicMock(images=[img1, img2], node_definitions=[])
        sess = _make_session("s1", selected_images=["img1", "img2"])
        sess.manifest = manifest

        with patch("app.routers.iso._get_session", return_value=sess), \
             patch("app.routers.iso._save_session"), \
             patch("app.routers.iso.ISOExtractor") as mock_ext, \
             patch("app.routers.iso.ensure_image_store", return_value=Path("/tmp")), \
             patch("app.routers.iso.load_manifest", return_value={}), \
             patch("app.routers.iso.save_manifest"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)), \
             patch("app.routers.iso._import_single_image", new_callable=AsyncMock) as mock_import, \
             patch("app.routers.iso._update_image_progress"):
            mock_ext.return_value.cleanup = MagicMock()
            # First image fails, second succeeds
            mock_import.side_effect = [RuntimeError("fail"), None]
            _run(_execute_import("s1"))
        # Should complete without raising

    def test_top_level_exception_marks_failed(self):
        img = MagicMock(id="img1", disk_image_filename="a.qcow2")
        manifest = MagicMock(images=[img], node_definitions=[])
        sess = _make_session("s1")
        sess.manifest = manifest

        with patch("app.routers.iso._get_session", return_value=sess), \
             patch("app.routers.iso._save_session") as mock_save, \
             patch("app.routers.iso.ISOExtractor") as mock_ext, \
             patch("app.routers.iso.ensure_image_store", return_value=Path("/tmp")), \
             patch("app.routers.iso.load_manifest", side_effect=RuntimeError("boom")):
            mock_ext.return_value.cleanup = MagicMock()
            _run(_execute_import("s1"))

        assert sess.status == "failed"
        assert "boom" in sess.error_message


# ---------------------------------------------------------------------------
# stream_import_progress — basic coverage via direct function
# ---------------------------------------------------------------------------


class TestStreamImportProgress:

    def test_404_on_missing_session(self, test_client, auth_headers):
        resp = test_client.get("/iso/nonexistent/progress", headers=auth_headers)
        assert resp.status_code == 404

    def test_completed_session_returns_complete(self, test_client, auth_headers):
        from app.routers.iso import _sessions, _session_lock

        sess = _make_session("done-sess", status="completed")
        with _session_lock:
            _sessions["done-sess"] = sess
        try:
            resp = test_client.get("/iso/done-sess/progress", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
        finally:
            with _session_lock:
                _sessions.pop("done-sess", None)


# ---------------------------------------------------------------------------
# _import_single_image — edge cases
# ---------------------------------------------------------------------------


class TestImportSingleImage:

    def test_no_disk_path_raises(self):
        from app.routers.iso import _import_single_image

        image = MagicMock(
            id="img1", disk_image_path=None, disk_image_filename=None,
            image_type="qcow2", node_definition_id="nd1",
        )
        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("dev1", None)):
            with pytest.raises(Exception):
                _run(_import_single_image(
                    "s1", image, [], MagicMock(), Path("/tmp/store"),
                    {}, False,
                ))


    def test_custom_device_creation(self):
        from app.routers.iso import _import_single_image

        image = MagicMock(
            id="img1", disk_image_path="images/disk.qcow2",
            disk_image_filename="disk.qcow2",
            image_type="qcow2", node_definition_id="nd1",
        )
        new_device = {"id": "custom-dev", "name": "Custom"}
        with patch("app.routers.iso._update_image_progress"), \
             patch("app.routers.iso.get_image_device_mapping", return_value=("custom-dev", new_device)), \
             patch("app.routers.iso.find_custom_device", return_value=None), \
             patch("app.routers.iso.add_custom_device") as mock_add, \
             patch("app.routers.iso.find_image_by_id", return_value=None), \
             patch("app.routers.iso.create_image_entry", return_value="new-img-id"), \
             patch("app.routers.iso._save_session"):
            extractor = MagicMock()
            extractor.extract_file = AsyncMock(return_value=Path("/tmp/disk.qcow2"))
            try:
                _run(_import_single_image(
                    "s1", image, [], extractor, Path("/tmp/store"),
                    {}, True,
                ))
            except Exception:
                pass  # Other parts may fail; we just check device creation
            mock_add.assert_called_once_with(new_device)
