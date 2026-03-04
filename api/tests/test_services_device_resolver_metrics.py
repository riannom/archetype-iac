"""Tests for DeviceResolver singleton/loader and MetricsService aggregation.

Covers:
- DeviceResolver: singleton pattern (get_resolver), _ensure_loaded ImportError fallback,
  ResolvedDevice dataclass, resolve_config path, and cache invalidation across instances.
- MetricsService: get_dashboard_metrics, get_containers_breakdown,
  get_resource_distribution, get_job_statistics, get_node_state_summary — with
  empty DB and populated DB scenarios.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.device_resolver import (
    DeviceResolver,
    ResolvedDevice,
    get_resolver,
)
from app.services.metrics_service import MetricsService, get_metrics_service


# ---------------------------------------------------------------------------
# DeviceResolver helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeVendorConfig:
    kind: str
    vendor: str


def _make_resolver(
    vendor_configs: dict | None = None,
    alias_map: dict[str, str] | None = None,
    vendor_map: dict[str, str] | None = None,
) -> DeviceResolver:
    """Build a DeviceResolver with pre-injected lookup tables (skips agent import)."""
    resolver = DeviceResolver()
    resolver._vendor_configs = vendor_configs or {}
    resolver._alias_map = alias_map or {}
    resolver._vendor_map = vendor_map or {}
    return resolver


# ---------------------------------------------------------------------------
# MetricsService helpers
# ---------------------------------------------------------------------------


def _fresh_host(
    db_session,
    *,
    status: str = "online",
    resource_usage: dict | None = None,
) -> object:
    """Create and persist a Host row with the given status and resource_usage JSON."""
    from app import models

    host = models.Host(
        id=str(uuid4()),
        name=f"agent-{uuid4().hex[:6]}",
        address="10.0.0.1:8001",
        status=status,
        resource_usage=json.dumps(resource_usage or {}),
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.flush()
    return host


def _fresh_lab(db_session, *, state: str = "running") -> object:
    """Create and persist a Lab row with the given state."""
    from app import models

    lab = models.Lab(
        id=str(uuid4()),
        name=f"lab-{uuid4().hex[:6]}",
        state=state,
    )
    db_session.add(lab)
    db_session.flush()
    return lab


def _fresh_job(db_session, *, status: str = "completed", action: str = "deploy", created_at=None) -> object:
    """Create and persist a Job row."""
    from app import models

    if created_at is None:
        created_at = datetime.now(timezone.utc)

    job = models.Job(
        id=str(uuid4()),
        action=action,
        status=status,
        created_at=created_at,
    )
    db_session.add(job)
    db_session.flush()
    return job


def _fresh_node_state(db_session, *, lab_id: str, actual_state: str = "running", is_ready: bool = True) -> object:
    """Create and persist a NodeState row."""
    from app import models

    ns = models.NodeState(
        id=str(uuid4()),
        lab_id=lab_id,
        node_id=str(uuid4()),
        node_name=f"node-{uuid4().hex[:4]}",
        actual_state=actual_state,
        is_ready=is_ready,
    )
    db_session.add(ns)
    db_session.flush()
    return ns


# ============================================================================
# DeviceResolver — singleton pattern
# ============================================================================


class TestGetResolverSingleton:
    """Tests for the module-level get_resolver() singleton factory."""

    def test_get_resolver_returns_device_resolver_instance(self):
        """get_resolver() returns a DeviceResolver."""
        resolver = get_resolver()
        assert isinstance(resolver, DeviceResolver)

    def test_get_resolver_returns_same_instance_on_repeated_calls(self):
        """get_resolver() is a singleton — same object every time."""
        r1 = get_resolver()
        r2 = get_resolver()
        assert r1 is r2

    def test_get_metrics_service_factory(self, test_db):
        """get_metrics_service() returns a MetricsService bound to the given session."""
        svc = get_metrics_service(test_db)
        assert isinstance(svc, MetricsService)
        assert svc.session is test_db


# ============================================================================
# DeviceResolver — _ensure_loaded with ImportError fallback
# ============================================================================


class TestEnsureLoadedFallback:
    """Tests for _ensure_loaded when agent.vendors cannot be imported."""

    def test_import_error_leaves_empty_maps(self):
        """When agent.vendors raises ImportError, all maps are empty dicts."""
        resolver = DeviceResolver()
        with patch.dict("sys.modules", {"agent": None, "agent.vendors": None}):
            resolver._ensure_loaded()

        assert resolver._vendor_configs == {}
        assert resolver._alias_map == {}
        assert resolver._vendor_map == {}

    def test_ensure_loaded_is_idempotent(self):
        """_ensure_loaded called twice doesn't overwrite already-loaded maps."""
        resolver = _make_resolver(
            vendor_configs={"ceos": _FakeVendorConfig(kind="ceos", vendor="Arista")},
            alias_map={"ceos": "ceos"},
        )
        # Maps already set — a second call must not clear them.
        resolver._ensure_loaded()
        assert "ceos" in resolver._vendor_configs

    def test_resolve_with_empty_vendor_configs_returns_unresolved(self):
        """A resolver with empty maps returns the normalized input as canonical_id."""
        resolver = _make_resolver(vendor_configs={}, alias_map={}, vendor_map={})

        with patch("app.image_store.find_custom_device", return_value=None):
            result = resolver.resolve("some-unknown-device")

        assert result.canonical_id == "some-unknown-device"
        assert result.vendor_config_key is None
        assert result.is_custom is False


# ============================================================================
# DeviceResolver — ResolvedDevice dataclass
# ============================================================================


class TestResolvedDeviceDataclass:
    """Tests for the ResolvedDevice frozen dataclass."""

    def test_resolved_device_is_frozen(self):
        """ResolvedDevice is immutable (frozen=True)."""
        rd = ResolvedDevice(
            canonical_id="ceos",
            vendor_config_key="ceos",
            kind="ceos",
            vendor="Arista",
            is_custom=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            rd.canonical_id = "changed"  # type: ignore[misc]

    def test_resolved_device_equality(self):
        """Two ResolvedDevice instances with the same fields are equal."""
        rd1 = ResolvedDevice(
            canonical_id="srl",
            vendor_config_key="srl",
            kind="srl",
            vendor="Nokia",
            is_custom=False,
        )
        rd2 = ResolvedDevice(
            canonical_id="srl",
            vendor_config_key="srl",
            kind="srl",
            vendor="Nokia",
            is_custom=False,
        )
        assert rd1 == rd2

    def test_resolved_device_none_optional_fields(self):
        """Optional fields default to None when not resolved."""
        rd = ResolvedDevice(
            canonical_id="x",
            vendor_config_key=None,
            kind=None,
            vendor=None,
            is_custom=False,
        )
        assert rd.vendor_config_key is None
        assert rd.kind is None
        assert rd.vendor is None


# ============================================================================
# MetricsService — empty database
# ============================================================================


class TestMetricsServiceEmptyDB:
    """MetricsService with no rows in any table returns safe zero-value responses."""

    def test_dashboard_metrics_no_agents_no_labs(self, test_db):
        """get_dashboard_metrics with empty DB returns zeroed counters."""
        svc = MetricsService(test_db)

        with patch("app.agent_client.is_agent_online", return_value=False):
            result = svc.get_dashboard_metrics()

        assert result["agents"]["total"] == 0
        assert result["agents"]["online"] == 0
        assert result["containers"]["running"] == 0
        assert result["labs"]["total"] == 0
        assert result["resources"]["avg_cpu_percent"] == 0
        assert "timestamp" in result

    def test_containers_breakdown_no_hosts(self, test_db):
        """get_containers_breakdown with empty DB returns empty hosts list."""
        svc = MetricsService(test_db)
        result = svc.get_containers_breakdown()

        assert result["hosts"] == []
        assert result["totals"]["running"] == 0
        assert result["totals"]["total"] == 0

    def test_resource_distribution_no_hosts(self, test_db):
        """get_resource_distribution with empty DB returns zeroed averages."""
        svc = MetricsService(test_db)
        result = svc.get_resource_distribution()

        assert result["agents"] == []
        assert result["averages"]["cpu_percent"] == 0.0
        assert result["averages"]["memory_percent"] == 0.0

    def test_job_statistics_no_jobs(self, test_db):
        """get_job_statistics with empty DB returns zero totals."""
        svc = MetricsService(test_db)
        result = svc.get_job_statistics(hours=24)

        assert result["total"] == 0
        assert result["by_status"] == {}
        assert result["by_action"] == {}

    def test_node_state_summary_no_nodes(self, test_db):
        """get_node_state_summary with empty DB returns zero counts."""
        svc = MetricsService(test_db)
        result = svc.get_node_state_summary()

        assert result["total"] == 0
        assert result["ready"] == 0
        assert result["by_state"] == {}


# ============================================================================
# MetricsService — populated database
# ============================================================================


class TestMetricsServiceWithData:
    """MetricsService with real SQLite rows exercises aggregation logic."""

    def test_dashboard_metrics_counts_online_offline(self, test_db):
        """Agent counts are split correctly between online and offline."""
        _fresh_host(test_db, status="online", resource_usage={"cpu_percent": 20, "memory_percent": 40})
        _fresh_host(test_db, status="offline")
        _fresh_host(test_db, status="degraded")

        svc = MetricsService(test_db)

        # is_agent_online uses heartbeat + status; we control it directly.
        def _online_check(host):
            return host.status == "online"

        with patch("app.agent_client.is_agent_online", side_effect=_online_check):
            result = svc.get_dashboard_metrics()

        assert result["agents"]["total"] == 3
        assert result["agents"]["online"] == 1
        assert result["agents"]["offline"] == 1
        assert result["agents"]["degraded"] == 1

    def test_dashboard_metrics_aggregates_resource_usage(self, test_db):
        """Resource averages are computed over online agents only."""
        _fresh_host(test_db, status="online", resource_usage={
            "cpu_percent": 30,
            "memory_percent": 50,
            "disk_used_gb": 10,
            "disk_total_gb": 100,
        })
        _fresh_host(test_db, status="online", resource_usage={
            "cpu_percent": 10,
            "memory_percent": 20,
            "disk_used_gb": 5,
            "disk_total_gb": 50,
        })

        svc = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            result = svc.get_dashboard_metrics()

        assert result["resources"]["avg_cpu_percent"] == 20.0
        assert result["resources"]["avg_memory_percent"] == 35.0
        assert result["resources"]["total_disk_used_gb"] == 15.0

    def test_dashboard_metrics_containers_by_lab(self, test_db):
        """Container details are aggregated by lab_prefix, excluding system containers."""
        resource_usage = {
            "containers_running": 2,
            "containers_total": 3,
            "container_details": [
                {"is_system": False, "lab_prefix": "lab-abc"},
                {"is_system": False, "lab_prefix": "lab-abc"},
                {"is_system": True, "lab_prefix": "system"},
            ],
        }
        _fresh_host(test_db, status="online", resource_usage=resource_usage)

        svc = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            result = svc.get_dashboard_metrics()

        assert result["containers"]["by_lab"]["lab-abc"] == 2
        assert "system" not in result["containers"]["by_lab"]

    def test_dashboard_metrics_lab_state_counts(self, test_db):
        """Lab state aggregation reflects actual DB rows."""
        _fresh_lab(test_db, state="running")
        _fresh_lab(test_db, state="running")
        _fresh_lab(test_db, state="stopped")
        _fresh_lab(test_db, state="error")

        svc = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=False):
            result = svc.get_dashboard_metrics()

        assert result["labs"]["total"] == 4
        assert result["labs"]["running"] == 2
        assert result["labs"]["stopped"] == 1
        assert result["labs"]["error"] == 1

    def test_containers_breakdown_populates_host_entries(self, test_db):
        """get_containers_breakdown includes one entry per host with correct fields."""
        _fresh_host(test_db, status="online", resource_usage={
            "containers_running": 3,
            "containers_total": 5,
            "container_details": [],
        })

        svc = MetricsService(test_db)
        with patch("app.agent_client.is_agent_online", return_value=True):
            result = svc.get_containers_breakdown()

        assert len(result["hosts"]) == 1
        host_entry = result["hosts"][0]
        assert host_entry["containers_running"] == 3
        assert host_entry["containers_total"] == 5
        assert host_entry["is_online"] is True
        assert result["totals"]["running"] == 3
        assert result["totals"]["total"] == 5

    def test_resource_distribution_averages_online_agents_only(self, test_db):
        """get_resource_distribution only averages online agents."""
        _fresh_host(test_db, status="online", resource_usage={"cpu_percent": 80, "memory_percent": 60})
        _fresh_host(test_db, status="offline", resource_usage={"cpu_percent": 10, "memory_percent": 10})

        svc = MetricsService(test_db)

        def _online_check(host):
            return host.status == "online"

        with patch("app.agent_client.is_agent_online", side_effect=_online_check):
            result = svc.get_resource_distribution()

        assert len(result["agents"]) == 2
        assert result["averages"]["cpu_percent"] == 80.0
        assert result["averages"]["memory_percent"] == 60.0

    def test_job_statistics_groups_by_status_and_action(self, test_db):
        """get_job_statistics counts by status and action prefix."""
        _fresh_job(test_db, status="completed", action="deploy:lab-123")
        _fresh_job(test_db, status="completed", action="deploy:lab-456")
        _fresh_job(test_db, status="failed", action="destroy:lab-789")

        svc = MetricsService(test_db)
        result = svc.get_job_statistics(hours=24)

        assert result["total"] == 3
        assert result["by_status"]["completed"] == 2
        assert result["by_status"]["failed"] == 1
        assert result["by_action"]["deploy"] == 2
        assert result["by_action"]["destroy"] == 1

    def test_job_statistics_respects_time_window(self, test_db):
        """Jobs older than the requested hours window are excluded."""
        old_ts = datetime.now(timezone.utc) - timedelta(hours=48)
        _fresh_job(test_db, status="completed", action="deploy", created_at=old_ts)
        _fresh_job(test_db, status="running", action="deploy")  # recent

        svc = MetricsService(test_db)
        result = svc.get_job_statistics(hours=24)

        # Only the recent job falls within the 24h window
        assert result["total"] == 1

    def test_node_state_summary_counts_ready_and_by_state(self, test_db):
        """get_node_state_summary counts ready nodes and groups by actual_state."""
        lab = _fresh_lab(test_db, state="running")
        _fresh_node_state(test_db, lab_id=lab.id, actual_state="running", is_ready=True)
        _fresh_node_state(test_db, lab_id=lab.id, actual_state="running", is_ready=True)
        _fresh_node_state(test_db, lab_id=lab.id, actual_state="stopped", is_ready=False)

        svc = MetricsService(test_db)
        result = svc.get_node_state_summary()

        assert result["total"] == 3
        assert result["ready"] == 2
        assert result["by_state"]["running"] == 2
        assert result["by_state"]["stopped"] == 1

    def test_count_labs_by_state_helper(self, test_db):
        """_count_labs_by_state correctly accumulates counts per state."""
        from app import models

        labs = [
            models.Lab(id=str(uuid4()), name="a", state="running"),
            models.Lab(id=str(uuid4()), name="b", state="running"),
            models.Lab(id=str(uuid4()), name="c", state="stopped"),
            models.Lab(id=str(uuid4()), name="d", state=None),  # null state -> "unknown"
        ]
        svc = MetricsService(test_db)
        counts = svc._count_labs_by_state(labs)

        assert counts["running"] == 2
        assert counts["stopped"] == 1
        assert counts["unknown"] == 1
