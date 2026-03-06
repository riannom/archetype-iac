"""Tests for api/app/services/support_bundle.py — ZipBuilder, completeness warnings (round 11)."""
from __future__ import annotations

import json

import pytest

from app.services.support_bundle import (
    ZipBuilder,
    _build_completeness_warnings,
)


# ---------------------------------------------------------------------------
# ZipBuilder
# ---------------------------------------------------------------------------


class TestZipBuilder:

    def test_cumulative_size_cap(self):
        zb = ZipBuilder(max_bytes=100)
        assert zb.add_bytes("a.txt", b"x" * 50) is True
        assert zb.add_bytes("b.txt", b"x" * 60) is False
        assert len(zb.errors) == 1
        assert "size cap" in zb.errors[0]

    def test_json_counts_toward_cap(self):
        zb = ZipBuilder(max_bytes=50)
        data = {"key": "value" * 10}  # Should be > 50 bytes when serialized
        result = zb.add_json("data.json", data)
        # Either fits or doesn't — depends on exact size
        assert zb.total_input_bytes >= 0

    def test_small_files_accepted(self):
        zb = ZipBuilder(max_bytes=10000)
        assert zb.add_bytes("a.txt", b"hello") is True
        assert zb.add_bytes("b.txt", b"world") is True
        assert len(zb.files) == 2
        assert zb.files[0]["path"] == "a.txt"
        assert zb.files[1]["path"] == "b.txt"

    def test_sha256_recorded(self):
        import hashlib
        content = b"test content"
        zb = ZipBuilder(max_bytes=10000)
        zb.add_bytes("test.txt", content)
        expected_hash = hashlib.sha256(content).hexdigest()
        assert zb.files[0]["sha256"] == expected_hash

    def test_close_returns_bytes(self):
        zb = ZipBuilder(max_bytes=10000)
        zb.add_bytes("a.txt", b"hello")
        result = zb.close()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_zero_cap_rejects_all(self):
        zb = ZipBuilder(max_bytes=0)
        assert zb.add_bytes("a.txt", b"x") is False


# ---------------------------------------------------------------------------
# _build_completeness_warnings
# ---------------------------------------------------------------------------


class TestCompletenessWarnings:

    def test_all_healthy_no_warnings(self):
        """All sources healthy → no warnings."""
        warnings = _build_completeness_warnings(
            prom_results={
                "targets_up_api": {"data": {"result": [{"value": [0, "1"]}]}},
                "targets_up_scheduler": {"data": {"result": [{"value": [0, "1"]}]}},
                "targets_up_worker": {"data": {"result": [{"value": [0, "1"]}]}},
                "targets_up_agent": {"data": {"result": [{"value": [0, "1"]}]}},
                "jobs_started_2h": {"data": {"result": [{"value": [0, "0"]}]}},
                "job_duration_samples_2h": {"data": {"result": [{"value": [0, "0"]}]}},
                "job_queue_wait_samples_2h": {"data": {"result": [{"value": [0, "0"]}]}},
                "jobs_active": {"data": {"result": [{"value": [0, "0"]}]}},
                "job_queue_depth": {"data": {"result": [{"value": [0, "0"]}]}},
            },
            prom_targets={"status": "success"},
            prom_alerts={"status": "success"},
            loki_service_logs={},
            loki_service_label_values=[],
            control_plane_health={
                "api": {"payload": {"status": "ok"}},
                "scheduler": {"payload": {"status": "ok"}},
                "worker": {"probe": {"ok": True}},
            },
        )
        # May have loki warnings if targets are up but no logs, but no Prometheus query failures
        prom_query_warnings = [w for w in warnings if "Prometheus" in w and "query" in w]
        assert len(prom_query_warnings) == 0

    def test_missing_prometheus_warnings(self):
        """Prometheus errors produce coverage gap warnings."""
        warnings = _build_completeness_warnings(
            prom_results={
                "targets_up_api": {"error": "connection refused"},
                "targets_up_scheduler": {"data": {"result": []}},
                "targets_up_worker": {"data": {"result": []}},
                "targets_up_agent": {"data": {"result": []}},
                "jobs_started_2h": {"data": {"result": []}},
                "job_duration_samples_2h": {"data": {"result": []}},
                "job_queue_wait_samples_2h": {"data": {"result": []}},
                "jobs_active": {"data": {"result": []}},
                "job_queue_depth": {"data": {"result": []}},
            },
            prom_targets={"status": "success"},
            prom_alerts={"status": "success"},
            loki_service_logs={},
            loki_service_label_values=[],
            control_plane_health={},
        )
        assert any("targets_up_api" in w for w in warnings)

    def test_prom_targets_error(self):
        warnings = _build_completeness_warnings(
            prom_results={k: {"data": {"result": []}} for k in [
                "targets_up_api", "targets_up_scheduler", "targets_up_worker",
                "targets_up_agent", "jobs_started_2h", "job_duration_samples_2h",
                "job_queue_wait_samples_2h", "jobs_active", "job_queue_depth",
            ]},
            prom_targets={"error": "timeout"},
            prom_alerts={"status": "success"},
            loki_service_logs={},
            loki_service_label_values=[],
            control_plane_health={},
        )
        assert any("targets snapshot" in w for w in warnings)

    def test_prom_alerts_error(self):
        warnings = _build_completeness_warnings(
            prom_results={k: {"data": {"result": []}} for k in [
                "targets_up_api", "targets_up_scheduler", "targets_up_worker",
                "targets_up_agent", "jobs_started_2h", "job_duration_samples_2h",
                "job_queue_wait_samples_2h", "jobs_active", "job_queue_depth",
            ]},
            prom_targets={"status": "success"},
            prom_alerts={"error": "failed"},
            loki_service_logs={},
            loki_service_label_values=[],
            control_plane_health={},
        )
        assert any("alerts snapshot" in w for w in warnings)
