"""Tests for ISO router endpoints."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestBrowseISOFiles:
    """Tests for GET /iso/browse endpoint."""

    def test_browse_empty_directory(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test browsing empty ISO directory."""
        from app.config import settings

        monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

        response = test_client.get("/iso/browse", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "upload_dir" in data
        assert "files" in data
        assert data["files"] == []

    def test_browse_with_iso_files(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test browsing directory with ISO files."""
        from app.config import settings

        monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

        # Create mock ISO files
        (tmp_path / "test1.iso").write_bytes(b"fake iso content 1")
        (tmp_path / "test2.iso").write_bytes(b"fake iso content 2")
        (tmp_path / "notiso.txt").write_bytes(b"not an iso")

        response = test_client.get("/iso/browse", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # Should only list .iso files
        iso_names = [f["name"] for f in data["files"]]
        assert "test1.iso" in iso_names
        assert "test2.iso" in iso_names
        assert "notiso.txt" not in iso_names

    def test_browse_unauthenticated(self, test_client: TestClient):
        """Test browse requires authentication."""
        response = test_client.get("/iso/browse")
        assert response.status_code == 401


class TestScanISO:
    """Tests for POST /iso/scan endpoint."""

    def test_scan_iso_file_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test scanning non-existent ISO file."""
        response = test_client.post(
            "/iso/scan",
            json={"iso_path": "/nonexistent/path.iso"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    @patch("app.routers.iso.check_7z_available")
    def test_scan_requires_7z(
        self,
        mock_check_7z,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
    ):
        """Test scan reports error when 7z is not available."""
        mock_check_7z.return_value = False

        iso_path = tmp_path / "test.iso"
        iso_path.write_bytes(b"fake iso")

        response = test_client.post(
            "/iso/scan",
            json={"iso_path": str(iso_path)},
            headers=auth_headers,
        )
        # Should fail since 7z is not available or ISO is invalid
        assert response.status_code in [400, 500]


class TestUploadInit:
    """Tests for POST /iso/upload/init endpoint."""

    def test_upload_init(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test initializing a chunked upload."""
        from app.config import settings

        monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

        response = test_client.post(
            "/iso/upload/init",
            json={
                "filename": "test.iso",
                "total_size": 1024 * 1024 * 100,  # 100MB
                "chunk_size": 1024 * 1024 * 10,  # 10MB chunks
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "upload_id" in data
        assert data["filename"] == "test.iso"
        assert data["total_chunks"] == 10

    def test_upload_init_default_chunk_size(
        self,
        test_client: TestClient,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test upload init uses default chunk size."""
        from app.config import settings

        monkeypatch.setattr(settings, "iso_upload_dir", str(tmp_path))

        response = test_client.post(
            "/iso/upload/init",
            json={
                "filename": "test.iso",
                "total_size": 1024 * 1024 * 50,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["chunk_size"] == 10 * 1024 * 1024  # Default 10MB


class TestUploadStatus:
    """Tests for GET /iso/upload/{upload_id}/status endpoint."""

    def test_upload_status_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test getting status for non-existent upload."""
        response = test_client.get(
            "/iso/upload/nonexistent/status", headers=auth_headers
        )
        assert response.status_code == 404


class TestSessionManagement:
    """Tests for ISO session endpoints."""

    def test_get_session_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test getting non-existent session."""
        response = test_client.get(
            "/iso/sessions/nonexistent", headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_session_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test deleting non-existent session."""
        response = test_client.delete(
            "/iso/sessions/nonexistent", headers=auth_headers
        )
        assert response.status_code == 404


class TestImportImages:
    """Tests for POST /iso/sessions/{session_id}/import endpoint."""

    def test_import_session_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test importing from non-existent session."""
        response = test_client.post(
            "/iso/sessions/nonexistent/import",
            json={"image_ids": ["img1"]},
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestGetImportProgress:
    """Tests for GET /iso/sessions/{session_id}/progress endpoint."""

    def test_import_progress_session_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test getting progress for non-existent session."""
        response = test_client.get(
            "/iso/sessions/nonexistent/progress", headers=auth_headers
        )
        assert response.status_code == 404


class TestListSessions:
    """Tests for GET /iso/{session_id} endpoint (session lookup)."""

    def test_get_nonexistent_session_returns_404(
        self,
        test_client: TestClient,
        auth_headers: dict,
    ):
        """Test that getting a non-existent session returns 404."""
        response = test_client.get("/iso/nonexistent-session", headers=auth_headers)
        assert response.status_code == 404


class TestISOEndpointAuthentication:
    """Tests for authentication on ISO endpoints."""

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/iso/browse", "GET"),
            ("/iso/scan", "POST"),
            ("/iso/upload/init", "POST"),
            ("/iso/sessions", "GET"),
        ],
    )
    def test_endpoints_require_auth(
        self, test_client: TestClient, endpoint: str, method: str
    ):
        """Test that all ISO endpoints require authentication."""
        if method == "GET":
            response = test_client.get(endpoint)
        else:
            response = test_client.post(endpoint, json={})

        assert response.status_code == 401
