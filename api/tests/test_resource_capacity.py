"""Tests for resource capacity validation service.

Covers: calculate_node_requirements, get_agent_capacity, check_capacity,
check_multihost_capacity, format_capacity_error, format_capacity_warnings,
and the GET /agents/detailed endpoint enrichment.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services.resource_capacity import (
    AgentBucket,
    AgentScore,
    CapacityCheckResult,
    DEFAULT_CPU_CORES,
    DEFAULT_MEMORY_MB,
    NodeRequirement,
    PlacementPlan,
    build_node_requirements,
    calculate_node_requirements,
    check_capacity,
    check_multihost_capacity,
    distribute_nodes_by_score,
    format_capacity_error,
    format_capacity_warnings,
    get_agent_capacity,
    plan_placement,
    score_agent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(
    id: str = "host-1",
    name: str = "Host 1",
    resource_usage: dict | None = None,
    capabilities: dict | None = None,
) -> models.Host:
    """Create a Host model instance without persisting to DB."""
    return models.Host(
        id=id,
        name=name,
        address="localhost:8080",
        status="online",
        resource_usage=json.dumps(resource_usage) if resource_usage is not None else None,
        capabilities=json.dumps(capabilities) if capabilities is not None else None,
        version="1.0.0",
    )


def _mock_vendor_configs(configs: dict | None = None):
    """Return a lambda suitable for monkeypatching _get_vendor_configs."""
    data = configs or {}
    return lambda: data


# ---------------------------------------------------------------------------
# 1. TestCalculateNodeRequirements
# ---------------------------------------------------------------------------


class TestCalculateNodeRequirements:
    def test_known_device_type(self, monkeypatch):
        cfg = SimpleNamespace(memory=2048, cpu=2)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"arista_ceos": cfg}),
        )
        reqs = calculate_node_requirements(["arista_ceos"])
        assert reqs.memory_mb == 2048
        assert reqs.cpu_cores == 2
        assert reqs.node_count == 1

    def test_unknown_device_type_uses_defaults(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        reqs = calculate_node_requirements(["totally_unknown"])
        assert reqs.memory_mb == DEFAULT_MEMORY_MB
        assert reqs.cpu_cores == DEFAULT_CPU_CORES
        assert reqs.node_count == 1

    def test_multiple_devices_aggregate(self, monkeypatch):
        cfg_a = SimpleNamespace(memory=2048, cpu=2)
        cfg_b = SimpleNamespace(memory=4096, cpu=4)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev_a": cfg_a, "dev_b": cfg_b}),
        )
        reqs = calculate_node_requirements(["dev_a", "dev_b", "dev_a"])
        assert reqs.memory_mb == 2048 + 4096 + 2048
        assert reqs.cpu_cores == 2 + 4 + 2
        assert reqs.node_count == 3

    def test_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        reqs = calculate_node_requirements([])
        assert reqs.memory_mb == 0
        assert reqs.cpu_cores == 0
        assert reqs.node_count == 0

    def test_vendor_configs_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        reqs = calculate_node_requirements(["arista_ceos", "srlinux"])
        assert reqs.memory_mb == DEFAULT_MEMORY_MB * 2
        assert reqs.cpu_cores == DEFAULT_CPU_CORES * 2

    def test_mixed_known_and_unknown(self, monkeypatch):
        cfg = SimpleNamespace(memory=512, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"tiny": cfg}),
        )
        reqs = calculate_node_requirements(["tiny", "unknown_dev"])
        assert reqs.memory_mb == 512 + DEFAULT_MEMORY_MB
        assert reqs.cpu_cores == 1 + DEFAULT_CPU_CORES
        assert reqs.node_count == 2


# ---------------------------------------------------------------------------
# 2. TestGetAgentCapacity
# ---------------------------------------------------------------------------


class TestGetAgentCapacity:
    def test_normal_heartbeat(self):
        host = _make_host(resource_usage={
            "memory_total_gb": 16,
            "memory_used_gb": 8,
            "cpu_percent": 50,
            "cpu_count": 8,
            "disk_total_gb": 500,
            "disk_used_gb": 200,
            "containers_running": 3,
        })
        cap = get_agent_capacity(host)
        assert cap.memory_total_mb == 16 * 1024
        assert cap.memory_used_mb == 8 * 1024
        assert cap.cpu_cores_total == 8
        assert cap.cpu_used_cores == pytest.approx(4.0)  # 8 * 50/100
        assert cap.disk_total_gb == 500
        assert cap.disk_used_gb == 200
        assert cap.containers_running == 3

    def test_empty_resource_usage(self):
        host = _make_host(resource_usage={})
        cap = get_agent_capacity(host)
        assert cap.memory_total_mb == 0
        assert cap.cpu_cores_total == 0
        assert cap.disk_total_gb == 0

    def test_null_resource_usage(self):
        host = models.Host(
            id="h1", name="h1", address="x:1", status="online",
            resource_usage=None, capabilities=None, version="1",
        )
        cap = get_agent_capacity(host)
        assert cap.memory_total_mb == 0

    def test_invalid_json(self):
        host = models.Host(
            id="h1", name="h1", address="x:1", status="online",
            resource_usage="not-json{", capabilities=None, version="1",
        )
        cap = get_agent_capacity(host)
        assert cap.memory_total_mb == 0
        assert cap.cpu_cores_total == 0

    def test_cpu_count_fallback_from_capabilities(self):
        host = _make_host(
            resource_usage={"cpu_percent": 25},
            capabilities={"cpu_count": 4},
        )
        cap = get_agent_capacity(host)
        assert cap.cpu_cores_total == 4
        assert cap.cpu_used_cores == pytest.approx(1.0)  # 4 * 25/100

    def test_cpu_used_calculation(self):
        host = _make_host(resource_usage={
            "cpu_percent": 75,
            "cpu_count": 12,
        })
        cap = get_agent_capacity(host)
        assert cap.cpu_used_cores == pytest.approx(9.0)

    def test_capacity_properties(self):
        host = _make_host(resource_usage={
            "memory_total_gb": 32,
            "memory_used_gb": 24,
            "cpu_count": 16,
            "cpu_percent": 50,
            "disk_total_gb": 1000,
            "disk_used_gb": 700,
        })
        cap = get_agent_capacity(host)
        assert cap.memory_available_mb == pytest.approx((32 - 24) * 1024)
        assert cap.cpu_available_cores == pytest.approx(8.0)
        assert cap.disk_available_gb == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# 3. TestCheckCapacity
# ---------------------------------------------------------------------------


class TestCheckCapacity:
    """Core logic tests for check_capacity().

    Uses monkeypatch to set deterministic thresholds.
    """

    def _set_thresholds(self, monkeypatch, **overrides):
        """Set resource thresholds via object.__setattr__ (pydantic compat)."""
        defaults = {
            "resource_validation_enabled": True,
            "resource_memory_buffer_mb": 0,
            "resource_cpu_buffer_cores": 0,
            "resource_disk_buffer_gb": 0,
            "resource_memory_warning_pct": 80.0,
            "resource_memory_error_pct": 95.0,
            "resource_cpu_warning_pct": 80.0,
            "resource_cpu_error_pct": 95.0,
            "resource_disk_warning_pct": 85.0,
            "resource_disk_error_pct": 95.0,
        }
        defaults.update(overrides)
        for key, val in defaults.items():
            object.__setattr__(settings, key, val)

    def _restore_defaults(self):
        """Restore production defaults after test."""
        defaults = {
            "resource_validation_enabled": True,
            "resource_memory_buffer_mb": 2048,
            "resource_cpu_buffer_cores": 1.0,
            "resource_disk_buffer_gb": 20.0,
            "resource_memory_warning_pct": 80.0,
            "resource_memory_error_pct": 95.0,
            "resource_cpu_warning_pct": 80.0,
            "resource_cpu_error_pct": 95.0,
            "resource_disk_warning_pct": 85.0,
            "resource_disk_error_pct": 95.0,
        }
        for key, val in defaults.items():
            object.__setattr__(settings, key, val)

    @pytest.fixture(autouse=True)
    def _cleanup_settings(self):
        yield
        self._restore_defaults()

    def test_validation_disabled(self, monkeypatch):
        self._set_thresholds(monkeypatch, resource_validation_enabled=False)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        host = _make_host(resource_usage={})
        result = check_capacity(host, ["arista_ceos"])
        assert result.fits is True
        assert result.errors == []
        assert result.warnings == []

    def test_no_heartbeat_data_skips_checks(self, monkeypatch):
        self._set_thresholds(monkeypatch)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        host = _make_host(resource_usage={})
        result = check_capacity(host, ["some_device"])
        # memory_total_mb=0 → skips checks, fits=True
        assert result.fits is True

    def test_fits_comfortably(self, monkeypatch):
        self._set_thresholds(monkeypatch)
        cfg = SimpleNamespace(memory=1024, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 32 GB total, 4 GB used → adding 1 GB → ~15.6% → well below 80%
        host = _make_host(resource_usage={
            "memory_total_gb": 32,
            "memory_used_gb": 4,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is True
        assert result.has_warnings is False
        assert result.errors == []
        assert result.warnings == []

    def test_memory_warning(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_memory_warning_pct=80.0,
                             resource_memory_error_pct=95.0)
        cfg = SimpleNamespace(memory=1024, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 10 GB total (10240 MB), 8 GB used (8192 MB), adding 1024 MB
        # projected = (8192 + 1024) / 10240 ≈ 90% → between 80-95%
        host = _make_host(resource_usage={
            "memory_total_gb": 10,
            "memory_used_gb": 8,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is True
        assert result.has_warnings is True
        assert len(result.warnings) == 1
        assert "Memory" in result.warnings[0]

    def test_memory_error(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_memory_warning_pct=80.0,
                             resource_memory_error_pct=95.0)
        cfg = SimpleNamespace(memory=1024, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 10 GB total, 9.5 GB used, adding 1 GB → projected ~102% → ≥ 95%
        host = _make_host(resource_usage={
            "memory_total_gb": 10,
            "memory_used_gb": 9.5,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is False
        assert len(result.errors) == 1
        assert "Memory" in result.errors[0]

    def test_cpu_warning(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_cpu_warning_pct=80.0,
                             resource_cpu_error_pct=95.0)
        cfg = SimpleNamespace(memory=512, cpu=2)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 10 CPUs, 75% used (7.5 cores), adding 2 → 9.5/10 = 95%? No, 95 ≥ 95 → error
        # Use: 10 CPUs, 70% (7 cores), adding 2 → 9/10 = 90% → warning
        host = _make_host(resource_usage={
            "memory_total_gb": 64,
            "memory_used_gb": 4,
            "cpu_count": 10,
            "cpu_percent": 70,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is True
        assert result.has_warnings is True
        assert any("CPU" in w for w in result.warnings)

    def test_cpu_error(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_cpu_warning_pct=80.0,
                             resource_cpu_error_pct=95.0)
        cfg = SimpleNamespace(memory=512, cpu=4)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 10 CPUs, 90% (9 cores), adding 4 → 13/10 = 130% → error
        host = _make_host(resource_usage={
            "memory_total_gb": 64,
            "memory_used_gb": 4,
            "cpu_count": 10,
            "cpu_percent": 90,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is False
        assert any("CPU" in e for e in result.errors)

    def test_disk_warning(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_disk_warning_pct=85.0,
                             resource_disk_error_pct=95.0)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        # disk: 90% used → between 85-95% → warning
        host = _make_host(resource_usage={
            "memory_total_gb": 64,
            "memory_used_gb": 4,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 100,
            "disk_used_gb": 90,
        })
        result = check_capacity(host, ["any_dev"])
        assert result.fits is True
        assert result.has_warnings is True
        assert any("Disk" in w for w in result.warnings)

    def test_disk_error(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_disk_warning_pct=85.0,
                             resource_disk_error_pct=95.0)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        # disk: 96% used → ≥ 95% → error
        host = _make_host(resource_usage={
            "memory_total_gb": 64,
            "memory_used_gb": 4,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 100,
            "disk_used_gb": 96,
        })
        result = check_capacity(host, ["any_dev"])
        assert result.fits is False
        assert any("Disk" in e for e in result.errors)

    def test_multiple_issues(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_memory_error_pct=95.0,
                             resource_disk_warning_pct=85.0,
                             resource_disk_error_pct=95.0)
        cfg = SimpleNamespace(memory=2048, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # Memory: 10 GB total, 9.5 GB used + 2 GB → error
        # Disk: 90% → warning
        host = _make_host(resource_usage={
            "memory_total_gb": 10,
            "memory_used_gb": 9.5,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 100,
            "disk_used_gb": 90,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is False
        assert any("Memory" in e for e in result.errors)
        assert any("Disk" in w for w in result.warnings)

    def test_buffer_reduces_usable_memory(self, monkeypatch):
        self._set_thresholds(monkeypatch,
                             resource_memory_buffer_mb=4096,
                             resource_memory_error_pct=95.0)
        cfg = SimpleNamespace(memory=1024, cpu=1)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"dev": cfg}),
        )
        # 10 GB (10240 MB) total, buffer 4096 MB → usable = 6144 MB
        # 9 GB (9216 MB) used + 1024 MB = 10240 MB → projected 100% → error
        host = _make_host(resource_usage={
            "memory_total_gb": 10,
            "memory_used_gb": 9,
            "cpu_count": 16,
            "cpu_percent": 10,
            "disk_total_gb": 500,
            "disk_used_gb": 100,
        })
        result = check_capacity(host, ["dev"])
        assert result.fits is False
        assert any("Memory" in e for e in result.errors)

    def test_agent_name_from_host(self, monkeypatch):
        self._set_thresholds(monkeypatch, resource_validation_enabled=False)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        host = _make_host(name="my-agent")
        result = check_capacity(host, [])
        assert result.agent_name == "my-agent"


# ---------------------------------------------------------------------------
# 4. TestCheckMultihostCapacity
# ---------------------------------------------------------------------------


class TestCheckMultihostCapacity:
    @pytest.fixture(autouse=True)
    def _set_enabled(self, monkeypatch):
        object.__setattr__(settings, "resource_validation_enabled", True)
        object.__setattr__(settings, "resource_memory_buffer_mb", 0)
        object.__setattr__(settings, "resource_cpu_buffer_cores", 0)
        object.__setattr__(settings, "resource_disk_buffer_gb", 0)
        object.__setattr__(settings, "resource_memory_warning_pct", 80.0)
        object.__setattr__(settings, "resource_memory_error_pct", 95.0)
        object.__setattr__(settings, "resource_cpu_warning_pct", 80.0)
        object.__setattr__(settings, "resource_cpu_error_pct", 95.0)
        object.__setattr__(settings, "resource_disk_warning_pct", 85.0)
        object.__setattr__(settings, "resource_disk_error_pct", 95.0)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        yield
        # Restore defaults
        object.__setattr__(settings, "resource_memory_buffer_mb", 2048)
        object.__setattr__(settings, "resource_cpu_buffer_cores", 1.0)
        object.__setattr__(settings, "resource_disk_buffer_gb", 20.0)

    def test_all_hosts_fit(self, test_db: Session):
        h1 = models.Host(
            id="mh-1", name="Agent A", address="a:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 64, "memory_used_gb": 8,
                "cpu_count": 16, "cpu_percent": 10,
                "disk_total_gb": 500, "disk_used_gb": 100,
            }),
        )
        h2 = models.Host(
            id="mh-2", name="Agent B", address="b:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 64, "memory_used_gb": 8,
                "cpu_count": 16, "cpu_percent": 10,
                "disk_total_gb": 500, "disk_used_gb": 100,
            }),
        )
        test_db.add_all([h1, h2])
        test_db.commit()

        results = check_multihost_capacity(
            {"mh-1": ["dev_a"], "mh-2": ["dev_b"]}, test_db
        )
        assert all(r.fits for r in results.values())

    def test_one_host_fails(self, test_db: Session):
        h1 = models.Host(
            id="mh-ok", name="Agent OK", address="a:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 64, "memory_used_gb": 8,
                "cpu_count": 16, "cpu_percent": 10,
                "disk_total_gb": 500, "disk_used_gb": 100,
            }),
        )
        h2 = models.Host(
            id="mh-full", name="Agent Full", address="b:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 10, "memory_used_gb": 9.8,
                "cpu_count": 4, "cpu_percent": 95,
                "disk_total_gb": 100, "disk_used_gb": 96,
            }),
        )
        test_db.add_all([h1, h2])
        test_db.commit()

        results = check_multihost_capacity(
            {"mh-ok": ["dev_a"], "mh-full": ["dev_b"]}, test_db
        )
        assert results["mh-ok"].fits is True
        assert results["mh-full"].fits is False

    def test_missing_host(self, test_db: Session):
        results = check_multihost_capacity(
            {"nonexistent-host": ["dev"]}, test_db
        )
        assert results["nonexistent-host"].fits is False
        assert any("not found" in e for e in results["nonexistent-host"].errors)

    def test_mixed_warnings_and_errors(self, test_db: Session):
        h1 = models.Host(
            id="mh-warn", name="Agent Warn", address="a:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 64, "memory_used_gb": 8,
                "cpu_count": 16, "cpu_percent": 10,
                "disk_total_gb": 100, "disk_used_gb": 90,  # 90% → warning
            }),
        )
        h2 = models.Host(
            id="mh-err", name="Agent Err", address="b:1", status="online", version="1",
            resource_usage=json.dumps({
                "memory_total_gb": 10, "memory_used_gb": 9.8,
                "cpu_count": 4, "cpu_percent": 10,
                "disk_total_gb": 500, "disk_used_gb": 100,
            }),
        )
        test_db.add_all([h1, h2])
        test_db.commit()

        results = check_multihost_capacity(
            {"mh-warn": ["dev"], "mh-err": ["dev"]}, test_db
        )
        assert results["mh-warn"].fits is True
        assert results["mh-warn"].has_warnings is True
        assert results["mh-err"].fits is False


# ---------------------------------------------------------------------------
# 5. TestFormatCapacityError
# ---------------------------------------------------------------------------


class TestFormatCapacityError:
    def test_single_host_error(self):
        results = {
            "h1": CapacityCheckResult(
                fits=False,
                agent_name="Agent-1",
                errors=["Memory: Need 4096 MB, only 1024 MB available (projected 98%)"],
            ),
        }
        msg = format_capacity_error(results)
        assert "Insufficient resources" in msg
        assert "Agent-1" in msg
        assert "Memory" in msg
        assert "Suggestions" in msg
        assert "Assign some nodes" in msg

    def test_multiple_hosts(self):
        results = {
            "h1": CapacityCheckResult(
                fits=False,
                agent_name="Agent-1",
                errors=["Memory: overloaded"],
            ),
            "h2": CapacityCheckResult(
                fits=False,
                agent_name="Agent-2",
                errors=["CPU: overloaded", "Disk: full"],
            ),
        }
        msg = format_capacity_error(results)
        assert "Agent-1" in msg
        assert "Agent-2" in msg
        assert "CPU" in msg
        assert "Disk" in msg

    def test_passing_hosts_excluded(self):
        results = {
            "h1": CapacityCheckResult(fits=True, agent_name="Agent-OK"),
            "h2": CapacityCheckResult(
                fits=False,
                agent_name="Agent-Bad",
                errors=["Memory: overloaded"],
            ),
        }
        msg = format_capacity_error(results)
        assert "Agent-OK" not in msg
        assert "Agent-Bad" in msg


# ---------------------------------------------------------------------------
# 6. TestFormatCapacityWarnings
# ---------------------------------------------------------------------------


class TestFormatCapacityWarnings:
    def test_multiple_host_warnings(self):
        results = {
            "h1": CapacityCheckResult(
                agent_name="Agent-1",
                warnings=["Memory: 85% projected"],
            ),
            "h2": CapacityCheckResult(
                agent_name="Agent-2",
                warnings=["CPU: 88% projected", "Disk: 87% used"],
            ),
        }
        warns = format_capacity_warnings(results)
        assert len(warns) == 3
        assert any("Agent-1" in w for w in warns)
        assert any("Agent-2" in w and "CPU" in w for w in warns)
        assert any("Agent-2" in w and "Disk" in w for w in warns)

    def test_no_warnings(self):
        results = {
            "h1": CapacityCheckResult(agent_name="Agent-1", warnings=[]),
            "h2": CapacityCheckResult(agent_name="Agent-2", warnings=[]),
        }
        assert format_capacity_warnings(results) == []


# ---------------------------------------------------------------------------
# 7. TestDetailedEndpointEnrichment
# ---------------------------------------------------------------------------


class TestDetailedEndpointEnrichment:
    """Tests for GET /agents/detailed enrichment of container_details,
    vm_details, images, and VM counts."""

    def test_container_details_enriched(
        self, test_client: TestClient, test_db: Session,
        admin_user: models.User, admin_auth_headers: dict,
    ):
        """Container details get lab_id and lab_name populated."""
        # Create a lab whose prefix matches container lab_prefix
        lab = models.Lab(
            name="My Lab", owner_id=admin_user.id, provider="docker", state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        lab_prefix = lab.id[:20]
        host = models.Host(
            id="det-agent-1", name="Det Agent", address="x:1",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["docker"]}),
            resource_usage=json.dumps({
                "cpu_percent": 10, "memory_percent": 20,
                "containers_running": 1, "containers_total": 1,
                "container_details": [{
                    "name": f"archetype-{lab_prefix}-r1",
                    "status": "running",
                    "lab_prefix": lab_prefix,
                    "is_system": False,
                }],
            }),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-1"][0]
        details = agent["resource_usage"]["container_details"]
        assert len(details) == 1
        assert details[0]["lab_id"] == lab.id
        assert details[0]["lab_name"] == "My Lab"

    def test_vm_details_enriched(
        self, test_client: TestClient, test_db: Session,
        admin_user: models.User, admin_auth_headers: dict,
    ):
        """VM details get lab_id and lab_name populated."""
        lab = models.Lab(
            name="VM Lab", owner_id=admin_user.id, provider="libvirt", state="running",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        lab_prefix = lab.id[:20]
        host = models.Host(
            id="det-agent-vm", name="VM Agent", address="x:2",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["libvirt"]}),
            resource_usage=json.dumps({
                "cpu_percent": 30, "memory_percent": 40,
                "vms_running": 1, "vms_total": 2,
                "vm_details": [{
                    "name": f"archetype-{lab_prefix}-fw1",
                    "status": "running",
                    "lab_prefix": lab_prefix,
                    "is_system": False,
                }],
            }),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=admin_auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-vm"][0]
        vm_details = agent["resource_usage"]["vm_details"]
        assert len(vm_details) == 1
        assert vm_details[0]["lab_id"] == lab.id
        assert vm_details[0]["lab_name"] == "VM Lab"

    def test_images_field_populated(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """ImageHost records appear in the images array."""
        host = models.Host(
            id="det-agent-img", name="Img Agent", address="x:3",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["docker"]}),
            resource_usage=json.dumps({"cpu_percent": 5}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        ih = models.ImageHost(
            image_id="docker:ceos:4.28.0F",
            host_id="det-agent-img",
            reference="ceos:4.28.0F",
            status="synced",
            synced_at=datetime.now(timezone.utc),
        )
        test_db.add(ih)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-img"][0]
        assert len(agent["images"]) == 1
        assert agent["images"][0]["image_id"] == "docker:ceos:4.28.0F"
        assert agent["images"][0]["status"] == "synced"

    def test_vm_counts_present(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """vms_running and vms_total fields appear in resource_usage."""
        host = models.Host(
            id="det-agent-vmc", name="VMC Agent", address="x:4",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["docker", "libvirt"]}),
            resource_usage=json.dumps({
                "cpu_percent": 10, "vms_running": 3, "vms_total": 5,
            }),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-vmc"][0]
        assert agent["resource_usage"]["vms_running"] == 3
        assert agent["resource_usage"]["vms_total"] == 5

    def test_empty_details(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Host with no container_details returns empty arrays."""
        host = models.Host(
            id="det-agent-empty", name="Empty Agent", address="x:5",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["docker"]}),
            resource_usage=json.dumps({"cpu_percent": 5}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-empty"][0]
        assert agent["resource_usage"]["container_details"] == []
        assert agent["resource_usage"]["vm_details"] == []
        assert agent["images"] == []

    def test_image_status_filtering(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Only synced/syncing/failed images appear, not 'unknown' or 'missing'."""
        host = models.Host(
            id="det-agent-filt", name="Filt Agent", address="x:6",
            status="online", version="1",
            capabilities=json.dumps({"providers": ["docker"]}),
            resource_usage=json.dumps({"cpu_percent": 5}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        for status in ["synced", "syncing", "failed", "unknown", "missing"]:
            ih = models.ImageHost(
                image_id=f"docker:img-{status}:1.0",
                host_id="det-agent-filt",
                reference=f"img-{status}:1.0",
                status=status,
            )
            test_db.add(ih)
        test_db.commit()

        response = test_client.get("/agents/detailed", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        agent = [a for a in data if a["id"] == "det-agent-filt"][0]
        image_statuses = {img["status"] for img in agent["images"]}
        # Only synced, syncing, failed should be present
        assert image_statuses == {"synced", "syncing", "failed"}
        assert len(agent["images"]) == 3


# ---------------------------------------------------------------------------
# 8. TestScoreAgent
# ---------------------------------------------------------------------------


class TestScoreAgent:
    """Tests for score_agent() placement scoring."""

    def _set_placement_settings(self, **overrides):
        defaults = {
            "placement_controller_reserve_mb": 4096,
            "placement_weight_memory": 0.7,
            "placement_weight_cpu": 0.3,
            "placement_local_penalty": 0.85,
            "placement_scoring_enabled": True,
        }
        defaults.update(overrides)
        for key, val in defaults.items():
            object.__setattr__(settings, key, val)

    def _restore_placement_settings(self):
        defaults = {
            "placement_controller_reserve_mb": 4096,
            "placement_weight_memory": 0.7,
            "placement_weight_cpu": 0.3,
            "placement_local_penalty": 0.85,
            "placement_scoring_enabled": True,
        }
        for key, val in defaults.items():
            object.__setattr__(settings, key, val)

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        self._restore_placement_settings()

    def test_no_heartbeat_returns_minimum(self):
        """Agent with no heartbeat data gets score 0.1."""
        host = _make_host(resource_usage={})
        result = score_agent(host)
        assert result.score == pytest.approx(0.1)
        assert "no heartbeat" in result.reason

    def test_local_penalty_applied(self):
        """Local agent gets score multiplied by penalty factor."""
        self._set_placement_settings()
        usage = {
            "memory_total_gb": 64, "memory_used_gb": 16,
            "cpu_count": 16, "cpu_percent": 25,
        }
        remote = _make_host(id="remote", resource_usage=usage)
        remote.is_local = False

        local = _make_host(id="local", resource_usage=usage)
        local.is_local = True

        remote_score = score_agent(remote)
        local_score = score_agent(local)

        # Local should be lower due to both reserve and penalty
        assert local_score.score < remote_score.score

    def test_more_memory_higher_score(self):
        """Agent with more available memory scores higher."""
        self._set_placement_settings()
        low_mem = _make_host(id="low", resource_usage={
            "memory_total_gb": 32, "memory_used_gb": 28,
            "cpu_count": 8, "cpu_percent": 50,
        })
        low_mem.is_local = False

        high_mem = _make_host(id="high", resource_usage={
            "memory_total_gb": 32, "memory_used_gb": 8,
            "cpu_count": 8, "cpu_percent": 50,
        })
        high_mem.is_local = False

        assert score_agent(high_mem).score > score_agent(low_mem).score

    def test_more_cpu_higher_score(self):
        """Agent with more available CPU scores higher."""
        self._set_placement_settings()
        low_cpu = _make_host(id="low", resource_usage={
            "memory_total_gb": 64, "memory_used_gb": 16,
            "cpu_count": 16, "cpu_percent": 90,
        })
        low_cpu.is_local = False

        high_cpu = _make_host(id="high", resource_usage={
            "memory_total_gb": 64, "memory_used_gb": 16,
            "cpu_count": 16, "cpu_percent": 10,
        })
        high_cpu.is_local = False

        assert score_agent(high_cpu).score > score_agent(low_cpu).score

    def test_score_clamped_to_zero_one(self):
        """Score never exceeds [0, 1] range."""
        self._set_placement_settings()
        # Fully available host
        host = _make_host(resource_usage={
            "memory_total_gb": 128, "memory_used_gb": 0,
            "cpu_count": 32, "cpu_percent": 0,
        })
        host.is_local = False
        result = score_agent(host)
        assert 0.0 <= result.score <= 1.0

    def test_zero_available_memory(self):
        """Agent with no available memory gets a low score."""
        self._set_placement_settings()
        host = _make_host(resource_usage={
            "memory_total_gb": 16, "memory_used_gb": 16,
            "cpu_count": 8, "cpu_percent": 50,
        })
        host.is_local = False
        result = score_agent(host)
        # Only CPU contributes: 0.3 * 0.5 = 0.15
        assert result.score == pytest.approx(0.15)

    def test_controller_reserve_subtracted_for_local(self):
        """Local agent has controller reserve subtracted from usable memory."""
        self._set_placement_settings(placement_controller_reserve_mb=8192)
        # 32 GB total, 8 GB used. Remote usable=32GB, local usable=32-8=24GB
        usage = {
            "memory_total_gb": 32, "memory_used_gb": 8,
            "cpu_count": 8, "cpu_percent": 0,
        }
        remote = _make_host(id="remote", resource_usage=usage)
        remote.is_local = False

        local = _make_host(id="local", resource_usage=usage)
        local.is_local = True

        # Remote: mem_avail = 32*1024 - 8*1024 = 24576, ratio = 24576/32768 = 0.75
        # Local: usable = 32768-8192 = 24576, avail = 24576-8192 = 16384, ratio = 16384/24576 ≈ 0.667
        # Plus local penalty. So local should be distinctly lower.
        assert score_agent(local).score < score_agent(remote).score


# ---------------------------------------------------------------------------
# 9. TestDistributeNodesByScore
# ---------------------------------------------------------------------------


class TestDistributeNodesByScore:
    """Tests for distribute_nodes_by_score() proportional distribution."""

    def test_equal_scores_equal_split(self):
        """Two agents with equal scores get equal node counts."""
        scores = {
            "a1": AgentScore(score=0.5),
            "a2": AgentScore(score=0.5),
        }
        result = distribute_nodes_by_score(["n1", "n2", "n3", "n4"], scores)
        counts = {}
        for agent_id in result.values():
            counts[agent_id] = counts.get(agent_id, 0) + 1
        assert counts["a1"] == 2
        assert counts["a2"] == 2

    def test_two_to_one_ratio(self):
        """Scores in 2:1 ratio produce ~67:33 split."""
        scores = {
            "a1": AgentScore(score=0.8),
            "a2": AgentScore(score=0.4),
        }
        result = distribute_nodes_by_score(
            ["n1", "n2", "n3", "n4", "n5", "n6"], scores
        )
        counts = {}
        for agent_id in result.values():
            counts[agent_id] = counts.get(agent_id, 0) + 1
        assert counts["a1"] == 4
        assert counts["a2"] == 2

    def test_single_node_goes_to_highest(self):
        """Single node is assigned to the highest-scoring agent."""
        scores = {
            "a1": AgentScore(score=0.3),
            "a2": AgentScore(score=0.9),
        }
        result = distribute_nodes_by_score(["n1"], scores)
        assert result["n1"] == "a2"

    def test_empty_inputs(self):
        """Empty node list or empty scores returns empty dict."""
        scores = {"a1": AgentScore(score=0.5)}
        assert distribute_nodes_by_score([], scores) == {}
        assert distribute_nodes_by_score(["n1"], {}) == {}

    def test_all_zero_scores(self):
        """All zero scores returns empty dict (no valid candidates)."""
        scores = {
            "a1": AgentScore(score=0.0),
            "a2": AgentScore(score=0.0),
        }
        result = distribute_nodes_by_score(["n1", "n2"], scores)
        assert result == {}

    def test_three_agents_unequal(self):
        """Three agents with varied scores get proportional allocation."""
        scores = {
            "a1": AgentScore(score=0.6),
            "a2": AgentScore(score=0.3),
            "a3": AgentScore(score=0.1),
        }
        result = distribute_nodes_by_score(
            [f"n{i}" for i in range(10)], scores
        )
        counts = {}
        for agent_id in result.values():
            counts[agent_id] = counts.get(agent_id, 0) + 1
        # 0.6/1.0 = 60% of 10 = 6, 0.3/1.0 = 30% of 10 = 3, 0.1/1.0 = 10% of 10 = 1
        assert counts["a1"] == 6
        assert counts["a2"] == 3
        assert counts["a3"] == 1

    def test_all_nodes_assigned(self):
        """Every node gets an assignment."""
        scores = {
            "a1": AgentScore(score=0.7),
            "a2": AgentScore(score=0.3),
        }
        nodes = [f"n{i}" for i in range(5)]
        result = distribute_nodes_by_score(nodes, scores)
        assert set(result.keys()) == set(nodes)


# ---------------------------------------------------------------------------
# 10. TestBuildNodeRequirements
# ---------------------------------------------------------------------------


class TestBuildNodeRequirements:
    def test_known_device(self, monkeypatch):
        cfg = SimpleNamespace(memory=4096, cpu=2)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"xrv9k": cfg}),
        )
        monkeypatch.setattr(
            "app.services.resource_capacity._get_device_overrides",
            lambda: {},
        )
        reqs = build_node_requirements([("r1", "xrv9k")])
        assert len(reqs) == 1
        assert reqs[0].node_name == "r1"
        assert reqs[0].device_type == "xrv9k"
        assert reqs[0].memory_mb == 4096
        assert reqs[0].cpu_cores == 2

    def test_unknown_device_defaults(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({}),
        )
        monkeypatch.setattr(
            "app.services.resource_capacity._get_device_overrides",
            lambda: {},
        )
        reqs = build_node_requirements([("s1", "mystery")])
        assert reqs[0].memory_mb == DEFAULT_MEMORY_MB
        assert reqs[0].cpu_cores == DEFAULT_CPU_CORES

    def test_multiple_mixed(self, monkeypatch):
        cfg = SimpleNamespace(memory=2048, cpu=2)
        monkeypatch.setattr(
            "app.services.resource_capacity._get_vendor_configs",
            _mock_vendor_configs({"ceos": cfg}),
        )
        monkeypatch.setattr(
            "app.services.resource_capacity._get_device_overrides",
            lambda: {},
        )
        reqs = build_node_requirements([("r1", "ceos"), ("r2", "unknown")])
        assert reqs[0].memory_mb == 2048
        assert reqs[1].memory_mb == DEFAULT_MEMORY_MB


# ---------------------------------------------------------------------------
# 11. TestPlanPlacement (Bin-Packing)
# ---------------------------------------------------------------------------


def _bucket(
    agent_id: str,
    name: str,
    mem_avail: float,
    cpu_avail: float,
    mem_total: float = 0,
    cpu_total: float = 0,
) -> AgentBucket:
    """Helper to create an AgentBucket."""
    return AgentBucket(
        agent_id=agent_id,
        agent_name=name,
        memory_available_mb=mem_avail,
        cpu_available_cores=cpu_avail,
        memory_total_mb=mem_total or mem_avail,
        cpu_total_cores=cpu_total or cpu_avail,
    )


def _node(name: str, mem: int = 2048, cpu: int = 1, device: str = "ceos") -> NodeRequirement:
    """Helper to create a NodeRequirement."""
    return NodeRequirement(node_name=name, device_type=device, memory_mb=mem, cpu_cores=cpu)


class TestPlanPlacement:
    """Tests for plan_placement() bin-packing algorithm."""

    def test_two_agents_balanced(self):
        """10 identical nodes across 2 equal agents → ~5 each."""
        agents = [
            _bucket("a1", "Agent-1", 20480, 10, 20480, 10),
            _bucket("a2", "Agent-2", 20480, 10, 20480, 10),
        ]
        nodes = [_node(f"n{i}") for i in range(10)]
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        assert not plan.errors
        assert len(plan.assignments) == 10
        counts = {}
        for aid in plan.assignments.values():
            counts[aid] = counts.get(aid, 0) + 1
        assert counts["a1"] == 5
        assert counts["a2"] == 5

    def test_heterogeneous_devices(self):
        """3 XRv9k (20GB) + 10 cEOS (2GB) across 2 agents (64GB + 32GB)."""
        agents = [
            _bucket("a1", "Agent-1", 65536, 32, 65536, 32),
            _bucket("a2", "Agent-2", 32768, 16, 32768, 16),
        ]
        # 3 * 20480 = 61440 MB, 10 * 2048 = 20480 MB. Total = 81920 < 98304
        heavy = [_node(f"xr{i}", mem=20480, cpu=4, device="xrv9k") for i in range(3)]
        light = [_node(f"ce{i}", mem=2048, cpu=1, device="ceos") for i in range(10)]
        plan = plan_placement(heavy + light, agents)
        assert not plan.unplaceable
        assert len(plan.assignments) == 13

    def test_overflow_to_second_agent(self):
        """First agent fills up, remaining nodes overflow to second."""
        # a1 has 6144 MB (fits 3 nodes), a2 has 6144 MB (fits 3 nodes)
        # 5 nodes x 2048 = 10240. After 3 on each, one agent overflows.
        agents = [
            _bucket("a1", "Agent-1", 6144, 8, 32768, 16),
            _bucket("a2", "Agent-2", 6144, 8, 32768, 16),
        ]
        nodes = [_node(f"n{i}") for i in range(5)]  # 5 x 2GB = 10GB needed
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        assert len(plan.assignments) == 5
        # Equal capacity → alternating: a1 gets placed first (or a2),
        # then they alternate as capacity decreases equally
        a1_count = sum(1 for a in plan.assignments.values() if a == "a1")
        a2_count = sum(1 for a in plan.assignments.values() if a == "a2")
        # Both should get nodes, with at most 1 difference
        assert abs(a1_count - a2_count) <= 1
        assert a1_count + a2_count == 5

    def test_node_too_large_for_any_agent(self):
        """Single node (20GB) with all agents < 20GB → clear error."""
        agents = [
            _bucket("a1", "Agent-1", 16384, 8),
            _bucket("a2", "Agent-2", 16384, 8),
        ]
        nodes = [_node("big-vm", mem=20480, cpu=4)]
        plan = plan_placement(nodes, agents)
        # Total cluster = 32768 > 20480, so cluster pre-check passes
        # But no single agent fits → unplaceable
        assert plan.unplaceable == ["big-vm"]
        assert len(plan.errors) == 1
        assert "no single agent" in plan.errors[0]

    def test_cluster_exhaustion(self):
        """Total nodes > total cluster → cluster-level error message."""
        agents = [
            _bucket("a1", "Agent-1", 8192, 4),
            _bucket("a2", "Agent-2", 8192, 4),
        ]
        # 10 nodes x 2048 = 20480 needed > 16384 available
        nodes = [_node(f"n{i}") for i in range(10)]
        plan = plan_placement(nodes, agents)
        assert len(plan.unplaceable) == 10
        assert len(plan.errors) == 1
        assert "insufficient cluster resources" in plan.errors[0]
        assert "Deficit" in plan.errors[0]

    def test_exact_fit(self):
        """Nodes exactly fill available capacity → succeeds."""
        agents = [_bucket("a1", "Agent-1", 4096, 4)]
        nodes = [_node("n1", mem=2048, cpu=2), _node("n2", mem=2048, cpu=2)]
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        assert len(plan.assignments) == 2
        assert all(a == "a1" for a in plan.assignments.values())

    def test_empty_nodes(self):
        """Empty node list → empty assignments."""
        agents = [_bucket("a1", "Agent-1", 8192, 4)]
        plan = plan_placement([], agents)
        assert plan.assignments == {}
        assert not plan.unplaceable
        assert not plan.errors

    def test_no_agents(self):
        """No agents available → all nodes unplaceable."""
        nodes = [_node("n1")]
        plan = plan_placement(nodes, [])
        assert plan.unplaceable == ["n1"]
        assert len(plan.errors) == 1
        assert "No agents" in plan.errors[0]

    def test_single_agent(self):
        """All nodes to single agent → works."""
        agents = [_bucket("a1", "Agent-1", 65536, 32)]
        nodes = [_node(f"n{i}") for i in range(20)]
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        assert all(a == "a1" for a in plan.assignments.values())

    def test_deterministic(self):
        """Same input always produces same output."""
        agents = [
            _bucket("a1", "Agent-1", 32768, 16, 32768, 16),
            _bucket("a2", "Agent-2", 32768, 16, 32768, 16),
        ]
        nodes = [_node(f"n{i}") for i in range(10)]

        plan1 = plan_placement(nodes, agents)
        plan2 = plan_placement(nodes, agents)
        assert plan1.assignments == plan2.assignments

    def test_controller_reserve(self):
        """Local agent has memory reserved → fewer nodes placed there."""
        agents = [
            _bucket("local", "Local-Agent", 16384, 8, 16384, 8),
            _bucket("remote", "Remote-Agent", 16384, 8, 16384, 8),
        ]
        nodes = [_node(f"n{i}") for i in range(8)]  # 8 x 2048 = 16384
        # With 4096 reserve on local: local has 12288 avail = 6 nodes
        # remote has 16384 avail = 8 nodes. Total needed = 16384.
        plan = plan_placement(
            nodes, agents,
            controller_reserve_mb=4096,
            local_agent_id="local",
        )
        assert not plan.unplaceable
        local_count = sum(1 for a in plan.assignments.values() if a == "local")
        remote_count = sum(1 for a in plan.assignments.values() if a == "remote")
        # Remote has more available memory, so it gets more nodes
        assert remote_count > local_count

    def test_per_agent_summary(self):
        """per_agent dict correctly groups node names by agent."""
        agents = [
            _bucket("a1", "Agent-1", 16384, 8),
            _bucket("a2", "Agent-2", 16384, 8),
        ]
        nodes = [_node(f"n{i}") for i in range(4)]
        plan = plan_placement(nodes, agents)
        assert "a1" in plan.per_agent or "a2" in plan.per_agent
        total_in_per_agent = sum(len(v) for v in plan.per_agent.values())
        assert total_in_per_agent == 4

    def test_warnings_on_tight_fit(self):
        """Agent with < 20% remaining memory after placement gets a warning."""
        agents = [_bucket("a1", "Agent-1", 2200, 4, 10000, 4)]
        nodes = [_node("n1", mem=2048, cpu=1)]
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        # After placing: 2200 - 2048 = 152 remaining = 1.5% of 10000
        assert len(plan.warnings) >= 1
        assert "remaining" in plan.warnings[0]

    def test_cpu_constraint(self):
        """Agent with enough memory but not enough CPU → node placed elsewhere."""
        agents = [
            _bucket("a1", "Agent-1", 65536, 2, 65536, 2),   # Lots of mem, 2 CPU
            _bucket("a2", "Agent-2", 65536, 8, 65536, 8),   # Lots of mem, 8 CPU
        ]
        nodes = [_node("n1", mem=4096, cpu=4)]  # Needs 4 CPUs
        plan = plan_placement(nodes, agents)
        assert not plan.unplaceable
        # a1 only has 2 CPUs, n1 needs 4 → must go to a2
        assert plan.assignments["n1"] == "a2"

    def test_largest_first_ordering(self):
        """Heavy nodes placed first ensure they get hosts with most capacity."""
        agents = [
            _bucket("a1", "Agent-1", 24576, 8, 24576, 8),  # 24 GB
            _bucket("a2", "Agent-2", 24576, 8, 24576, 8),  # 24 GB
        ]
        # 1 heavy (20 GB) + 4 light (2 GB each = 8 GB)
        heavy = [_node("heavy", mem=20480, cpu=4)]
        light = [_node(f"light{i}", mem=2048, cpu=1) for i in range(4)]
        plan = plan_placement(heavy + light, agents)
        assert not plan.unplaceable
        # Heavy gets placed first on one agent, light fills remaining
        assert len(plan.assignments) == 5
