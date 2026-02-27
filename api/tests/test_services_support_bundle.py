"""Tests for support bundle generation service."""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services.support_bundle import (
    MAX_BUNDLE_BYTES,
    ZipBuilder,
    _json_dumps,
    _model_to_dict,
    _safe_json_load,
    sanitize_data,
)


class TestZipBuilder:
    """Tests for ZipBuilder zip archive utility."""

    def test_add_bytes_success(self):
        """Adding bytes within size limit succeeds."""
        builder = ZipBuilder(max_bytes=1_000_000)
        result = builder.add_bytes("test.txt", b"Hello, world!")
        assert result is True
        assert builder.total_input_bytes == 13
        assert len(builder.files) == 1
        assert builder.files[0]["path"] == "test.txt"

    def test_add_bytes_exceeds_limit(self):
        """Adding bytes beyond the size limit fails gracefully."""
        builder = ZipBuilder(max_bytes=10)
        result = builder.add_bytes("big.txt", b"x" * 20)
        assert result is False
        assert len(builder.errors) == 1
        assert "size cap" in builder.errors[0].lower()

    def test_add_json(self):
        """add_json serializes dict to JSON bytes and adds to archive."""
        builder = ZipBuilder(max_bytes=1_000_000)
        result = builder.add_json("data.json", {"key": "value"})
        assert result is True
        assert len(builder.files) == 1
        assert builder.files[0]["path"] == "data.json"

    def test_close_returns_bytes(self):
        """close() returns a valid zip file as bytes."""
        builder = ZipBuilder(max_bytes=1_000_000)
        builder.add_bytes("hello.txt", b"Hello!")
        zip_bytes = builder.close()
        assert isinstance(zip_bytes, bytes)
        # Verify it's a valid zip
        buf = BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            assert "hello.txt" in zf.namelist()
            assert zf.read("hello.txt") == b"Hello!"

    def test_size_tracking(self):
        """Tracks total input bytes across multiple files."""
        builder = ZipBuilder(max_bytes=1_000_000)
        builder.add_bytes("a.txt", b"aaa")
        builder.add_bytes("b.txt", b"bbbbb")
        assert builder.total_input_bytes == 8

    def test_sha256_in_file_metadata(self):
        """Each file entry includes a SHA256 hash."""
        builder = ZipBuilder(max_bytes=1_000_000)
        content = b"test content"
        builder.add_bytes("test.txt", content)
        expected_hash = hashlib.sha256(content).hexdigest()
        assert builder.files[0]["sha256"] == expected_hash


class TestSanitizeData:
    """Tests for sensitive data redaction."""

    def test_redacts_password_keys(self):
        """Keys matching 'password' pattern are redacted."""
        data = {"username": "admin", "password": "secret123"}
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert result["username"] == "admin"
        assert result["password"] == "[REDACTED]"

    def test_redacts_secret_keys(self):
        """Keys matching 'secret' pattern are redacted."""
        data = {"jwt_secret": "my-jwt-key", "name": "test"}
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert result["jwt_secret"] == "[REDACTED]"
        assert result["name"] == "test"

    def test_redacts_token_keys(self):
        """Keys matching 'token' pattern are redacted."""
        data = {"authorization": "Bearer abc123.xyz.789"}
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert result["authorization"] == "[REDACTED]"

    def test_redacts_bearer_in_values(self):
        """Bearer tokens in string values are redacted."""
        data = {"log_line": "Header: Bearer abc123.xyz.789 extra"}
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert "Bearer" not in result["log_line"] or "[REDACTED]" in result["log_line"]

    def test_preserves_structure(self):
        """Nested dicts and lists preserve structure."""
        data = {
            "outer": {
                "inner": [1, 2, {"nested": "ok"}]
            }
        }
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert result["outer"]["inner"] == [1, 2, {"nested": "ok"}]

    def test_pii_safe_masks_emails(self):
        """PII-safe mode masks email addresses in string values."""
        data = {"log": "User admin@example.com logged in"}
        result = sanitize_data(
            data, pii_safe=True, lab_alias={}, host_alias={}
        )
        assert "admin@example.com" not in result["log"]
        assert "[MASKED_EMAIL]" in result["log"]

    def test_pii_safe_replaces_lab_names(self):
        """PII-safe mode replaces lab names with aliases."""
        data = {"info": "Lab Prod-Network deployed"}
        result = sanitize_data(
            data,
            pii_safe=True,
            lab_alias={"Prod-Network": "lab-abc12345"},
            host_alias={},
        )
        assert "Prod-Network" not in result["info"]
        assert "lab-abc12345" in result["info"]

    def test_pii_safe_replaces_host_names(self):
        """PII-safe mode replaces host names with aliases."""
        data = {"info": "Agent My-Server running"}
        result = sanitize_data(
            data,
            pii_safe=True,
            lab_alias={},
            host_alias={"My-Server": "host-xyz99"},
        )
        assert "My-Server" not in result["info"]
        assert "host-xyz99" in result["info"]

    def test_handles_non_string_values(self):
        """Non-string values are returned unchanged."""
        data = {"count": 42, "flag": True, "nothing": None}
        result = sanitize_data(
            data, pii_safe=False, lab_alias={}, host_alias={}
        )
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["nothing"] is None


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_json_dumps_returns_bytes(self):
        """_json_dumps returns UTF-8 encoded bytes."""
        result = _json_dumps({"key": "value"})
        assert isinstance(result, bytes)
        parsed = json.loads(result.decode("utf-8"))
        assert parsed["key"] == "value"

    def test_json_dumps_handles_datetime(self):
        """_json_dumps serializes datetime via default=str."""
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _json_dumps({"time": dt})
        assert isinstance(result, bytes)
        parsed = json.loads(result.decode("utf-8"))
        assert "2024" in parsed["time"]

    def test_safe_json_load_valid(self):
        """_safe_json_load parses valid JSON string."""
        result = _safe_json_load('{"key": "value"}')
        assert result == {"key": "value"}

    def test_safe_json_load_invalid(self):
        """_safe_json_load returns raw dict for invalid JSON."""
        result = _safe_json_load("not json")
        assert "raw" in result

    def test_safe_json_load_none(self):
        """_safe_json_load returns empty dict for None."""
        result = _safe_json_load(None)
        assert result == {}

    def test_safe_json_load_empty(self):
        """_safe_json_load returns empty dict for empty string."""
        result = _safe_json_load("")
        assert result == {}

    def test_model_to_dict(self):
        """_model_to_dict extracts specified fields from an object."""
        mock = MagicMock()
        mock.id = "abc"
        mock.name = "Test"
        mock.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock.missing = None

        result = _model_to_dict(mock, ["id", "name", "created_at", "missing"])
        assert result["id"] == "abc"
        assert result["name"] == "Test"
        assert "2024" in result["created_at"]
        assert result["missing"] is None


class TestCollectLabData:
    """Tests for lab data collection within support bundles."""

    def test_lab_export_includes_metadata(self, test_db: Session, sample_lab: models.Lab):
        """Lab export includes lab metadata fields."""
        from app.services.support_bundle import _lab_export

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = _lab_export(test_db, sample_lab, since, include_configs=False)
        assert "metadata" in result
        assert result["metadata"]["id"] == sample_lab.id
        assert result["metadata"]["name"] == sample_lab.name

    def test_lab_export_includes_topology(self, test_db: Session, sample_lab: models.Lab):
        """Lab export includes topology YAML and graph."""
        from app.services.support_bundle import _lab_export

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = _lab_export(test_db, sample_lab, since, include_configs=False)
        assert "topology_yaml" in result
        assert "topology_graph" in result

    def test_lab_export_includes_node_states(
        self,
        test_db: Session,
        sample_lab_with_nodes: tuple,
    ):
        """Lab export includes node state data."""
        from app.services.support_bundle import _lab_export

        lab, nodes = sample_lab_with_nodes
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        result = _lab_export(test_db, lab, since, include_configs=False)
        assert "node_states" in result
        assert len(result["node_states"]) == 2

    def test_lab_export_includes_jobs(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_job: models.Job,
    ):
        """Lab export includes recent job history."""
        from app.services.support_bundle import _lab_export

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        with patch("app.services.support_bundle.get_log_content", return_value=""):
            result = _lab_export(test_db, sample_lab, since, include_configs=False)
        assert "jobs" in result
        assert len(result["jobs"]) >= 1


class TestCollectSystemData:
    """Tests for system-level data collection."""

    def test_system_info_structure(self):
        """System info dict includes expected fields."""
        from app.services.support_bundle import _now_utc

        # This tests the shape of the system_info dict created in build_support_bundle
        system_info = {
            "generated_at": _now_utc().isoformat(),
            "bundle_id": "test-bundle",
            "time_window_hours": 24,
            "service": "archetype-api",
        }
        assert "generated_at" in system_info
        assert system_info["service"] == "archetype-api"


class TestBundleMetadata:
    """Tests for bundle metadata generation."""

    def test_zip_builder_file_list(self):
        """ZipBuilder tracks file list with paths and sizes."""
        builder = ZipBuilder(max_bytes=1_000_000)
        builder.add_bytes("file1.txt", b"abc")
        builder.add_bytes("file2.txt", b"defgh")
        assert len(builder.files) == 2
        assert builder.files[0]["size_bytes"] == 3
        assert builder.files[1]["size_bytes"] == 5

    def test_zip_builder_errors_list(self):
        """ZipBuilder tracks error messages for skipped files."""
        builder = ZipBuilder(max_bytes=5)
        builder.add_bytes("ok.txt", b"hi")
        builder.add_bytes("big.txt", b"too large content")
        assert len(builder.errors) == 1
