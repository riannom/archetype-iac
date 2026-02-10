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
    CapacityCheckResult,
    DEFAULT_CPU_CORES,
    DEFAULT_MEMORY_MB,
    calculate_node_requirements,
    check_capacity,
    check_multihost_capacity,
    format_capacity_error,
    format_capacity_warnings,
    get_agent_capacity,
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
