"""Tests for agent/main.py — lifespan, metrics helpers, and snapshotter detection.

Covers:
- lifespan() async context manager startup/shutdown paths
- Redis lock manager init (success + fallback to NoopDeployLockManager)
- Individual startup component failures (non-fatal)
- _parse_metrics_allowlist — various input formats
- _client_host_from_request — with/without X-Forwarded-For
- _is_metrics_client_allowed — allowed/denied clients
- _parse_driver_status — normalization helper
- _classify_docker_snapshotter_mode — mode classification
- _log_docker_snapshotter_mode_at_startup — drift detection
"""

from __future__ import annotations

import asyncio
import ipaddress
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.main import (
    _classify_docker_snapshotter_mode,
    _client_host_from_request,
    _is_metrics_client_allowed,
    _parse_driver_status,
    _parse_metrics_allowlist,
)


# ---------------------------------------------------------------------------
# 1. _parse_driver_status
# ---------------------------------------------------------------------------


class TestParseDriverStatus:
    """Tests for _parse_driver_status()."""

    def test_list_of_pairs(self):
        """Parses a list of [key, value] pairs."""
        result = _parse_driver_status([["Backing Filesystem", "extfs"], ["Supports d_type", "true"]])
        assert result == {"Backing Filesystem": "extfs", "Supports d_type": "true"}

    def test_empty_list(self):
        result = _parse_driver_status([])
        assert result == {}

    def test_non_list_input(self):
        """Non-list returns empty dict."""
        assert _parse_driver_status(None) == {}
        assert _parse_driver_status("not a list") == {}
        assert _parse_driver_status(42) == {}

    def test_mixed_valid_and_invalid_items(self):
        """Items not exactly length 2 are skipped."""
        result = _parse_driver_status([
            ["k1", "v1"],
            ["too", "many", "items"],
            ["k2", "v2"],
            "not-a-list",
        ])
        assert result == {"k1": "v1", "k2": "v2"}

    def test_tuples_accepted(self):
        """Tuples work the same as lists."""
        result = _parse_driver_status([("key", "value")])
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# 2. _classify_docker_snapshotter_mode
# ---------------------------------------------------------------------------


class TestClassifyDockerSnapshotterMode:
    """Tests for _classify_docker_snapshotter_mode()."""

    def test_containerd_mode(self):
        assert _classify_docker_snapshotter_mode(
            "overlay2", "io.containerd.snapshotter.v1.overlayfs"
        ) == "containerd"

    def test_legacy_no_driver_type(self):
        assert _classify_docker_snapshotter_mode("overlay2", None) == "legacy"

    def test_legacy_overlay2_no_snapshotter(self):
        assert _classify_docker_snapshotter_mode("overlay2", "io.containerd.content.v1") == "legacy"

    def test_legacy_overlayfs(self):
        assert _classify_docker_snapshotter_mode("overlayfs", "io.containerd.content.v1") == "legacy"

    def test_unknown_driver(self):
        assert _classify_docker_snapshotter_mode("btrfs", "some-unknown-type") == "unknown"


# ---------------------------------------------------------------------------
# 3. _parse_metrics_allowlist
# ---------------------------------------------------------------------------


class TestParseMetricsAllowlist:
    """Tests for _parse_metrics_allowlist()."""

    def test_empty_string(self):
        networks, literals = _parse_metrics_allowlist("")
        assert networks == ()
        assert literals == frozenset()

    def test_single_cidr(self):
        networks, literals = _parse_metrics_allowlist("10.0.0.0/8")
        assert len(networks) == 1
        assert networks[0] == ipaddress.ip_network("10.0.0.0/8")
        assert literals == frozenset()

    def test_multiple_cidrs(self):
        networks, literals = _parse_metrics_allowlist("10.0.0.0/8, 172.16.0.0/12")
        assert len(networks) == 2

    def test_literal_hosts(self):
        networks, literals = _parse_metrics_allowlist("localhost, testclient")
        assert networks == ()
        assert literals == frozenset({"localhost", "testclient"})

    def test_mixed_cidrs_and_literals(self):
        networks, literals = _parse_metrics_allowlist("10.0.0.0/8, localhost, 192.168.0.0/16")
        assert len(networks) == 2
        assert "localhost" in literals

    def test_ipv6_cidr(self):
        networks, literals = _parse_metrics_allowlist("::1/128")
        assert len(networks) == 1
        assert networks[0] == ipaddress.ip_network("::1/128")

    def test_whitespace_handling(self):
        networks, literals = _parse_metrics_allowlist("  10.0.0.0/8 ,  ,  localhost  ")
        assert len(networks) == 1
        assert "localhost" in literals

    def test_caching(self):
        """Repeated calls with same input should return cached result."""
        _parse_metrics_allowlist.cache_clear()
        r1 = _parse_metrics_allowlist("10.0.0.0/8")
        r2 = _parse_metrics_allowlist("10.0.0.0/8")
        assert r1 is r2


# ---------------------------------------------------------------------------
# 4. _client_host_from_request
# ---------------------------------------------------------------------------


class TestClientHostFromRequest:
    """Tests for _client_host_from_request()."""

    def test_direct_client_host(self):
        req = MagicMock()
        req.client = MagicMock()
        req.client.host = "192.168.1.100"
        req.headers = {}
        assert _client_host_from_request(req) == "192.168.1.100"

    def test_x_forwarded_for_fallback(self):
        """When no client.host, use X-Forwarded-For."""
        req = MagicMock()
        req.client = None
        req.headers = {"x-forwarded-for": "10.0.0.1, 10.0.0.2"}
        assert _client_host_from_request(req) == "10.0.0.1"

    def test_x_forwarded_for_single(self):
        req = MagicMock()
        req.client = None
        req.headers = {"x-forwarded-for": "172.16.0.5"}
        assert _client_host_from_request(req) == "172.16.0.5"

    def test_no_client_no_forwarded(self):
        req = MagicMock()
        req.client = None
        req.headers = {}
        assert _client_host_from_request(req) is None

    def test_client_with_no_host_attr(self):
        """Client exists but has no host attribute — falls through."""
        req = MagicMock()
        req.client = MagicMock(spec=[])  # no 'host' attribute
        req.headers = {"x-forwarded-for": "10.0.0.1"}
        assert _client_host_from_request(req) == "10.0.0.1"

    def test_empty_forwarded_header(self):
        req = MagicMock()
        req.client = None
        req.headers = {"x-forwarded-for": ""}
        assert _client_host_from_request(req) is None

    def test_client_host_is_none(self):
        """client exists, client.host is None."""
        req = MagicMock()
        req.client.host = None
        req.headers = {"x-forwarded-for": "10.0.0.9"}
        assert _client_host_from_request(req) == "10.0.0.9"


# ---------------------------------------------------------------------------
# 5. _is_metrics_client_allowed
# ---------------------------------------------------------------------------


class TestIsMetricsClientAllowed:
    """Tests for _is_metrics_client_allowed()."""

    def test_empty_allowlist_allows_all(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("8.8.8.8") is True

    def test_star_allows_all(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "*")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("8.8.8.8") is True

    def test_matching_cidr(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("10.1.2.3") is True

    def test_non_matching_cidr(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("192.168.1.1") is False

    def test_literal_host_match(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "localhost")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("localhost") is True

    def test_literal_host_case_insensitive(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "LocalHost")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("LOCALHOST") is True

    def test_none_client_denied(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed(None) is False

    def test_empty_string_client_denied(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("") is False

    def test_invalid_ip_denied(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("not-an-ip") is False

    def test_ipv6_in_ipv6_cidr(self, monkeypatch):
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "::1/128")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("::1") is True

    def test_ipv4_not_in_ipv6_cidr(self, monkeypatch):
        """IPv4 address should not match IPv6 CIDR."""
        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "::1/128")
        _parse_metrics_allowlist.cache_clear()
        assert _is_metrics_client_allowed("127.0.0.1") is False


# ---------------------------------------------------------------------------
# 6. _log_docker_snapshotter_mode_at_startup
# ---------------------------------------------------------------------------


class TestLogDockerSnapshotterMode:
    """Tests for _log_docker_snapshotter_mode_at_startup()."""

    @pytest.mark.asyncio
    async def test_skipped_when_docker_disabled(self, monkeypatch):
        from agent.config import settings
        from agent.main import _log_docker_snapshotter_mode_at_startup
        monkeypatch.setattr(settings, "enable_docker", False)
        # Should return without error
        await _log_docker_snapshotter_mode_at_startup()

    @pytest.mark.asyncio
    async def test_invalid_expected_mode_falls_back(self, monkeypatch):
        from agent.config import settings
        from agent.main import _log_docker_snapshotter_mode_at_startup

        monkeypatch.setattr(settings, "enable_docker", True)
        monkeypatch.setattr(settings, "docker_snapshotter_expected_mode", "invalid_mode")

        mock_client = MagicMock()
        mock_client.info.return_value = {"Driver": "overlay2", "DriverStatus": []}
        monkeypatch.setattr("agent.main.get_docker_client", lambda: mock_client)

        # Should not raise
        await _log_docker_snapshotter_mode_at_startup()

    @pytest.mark.asyncio
    async def test_docker_info_failure(self, monkeypatch):
        from agent.config import settings
        from agent.main import _log_docker_snapshotter_mode_at_startup

        monkeypatch.setattr(settings, "enable_docker", True)
        monkeypatch.setattr(settings, "docker_snapshotter_expected_mode", "any")
        monkeypatch.setattr("agent.main.get_docker_client", MagicMock(side_effect=RuntimeError("no docker")))

        # Should not raise
        await _log_docker_snapshotter_mode_at_startup()

    @pytest.mark.asyncio
    async def test_drift_detection_legacy_vs_containerd(self, monkeypatch):
        from agent.config import settings
        from agent.main import _log_docker_snapshotter_mode_at_startup

        monkeypatch.setattr(settings, "enable_docker", True)
        monkeypatch.setattr(settings, "docker_snapshotter_expected_mode", "legacy")

        mock_client = MagicMock()
        mock_client.info.return_value = {
            "Driver": "overlay2",
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1.overlayfs"]],
        }
        monkeypatch.setattr("agent.main.get_docker_client", lambda: mock_client)

        # Should detect drift but not raise
        await _log_docker_snapshotter_mode_at_startup()


# ---------------------------------------------------------------------------
# 7. lifespan() context manager
# ---------------------------------------------------------------------------


class TestLifespan:
    """Tests for the lifespan() async context manager."""

    @pytest.mark.asyncio
    async def test_testing_mode_skips_startup(self, monkeypatch):
        """When ARCHETYPE_AGENT_TESTING=1, lifespan yields immediately."""
        from agent.main import lifespan

        monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")

        app = MagicMock()
        async with lifespan(app):
            pass  # Should work without errors

    @pytest.mark.asyncio
    async def test_redis_unavailable_falls_back_to_noop(self, monkeypatch):
        """When Redis ping fails, should fall back to NoopDeployLockManager."""
        from agent.main import lifespan
        import agent.agent_state as _state

        monkeypatch.delenv("ARCHETYPE_AGENT_TESTING", raising=False)

        # Mock check_and_rollback
        monkeypatch.setattr("agent.main.check_and_rollback", lambda: None)

        # Mock capabilities/backend
        monkeypatch.setattr("agent.main.get_capabilities", lambda: ["docker"])
        mock_backend = AsyncMock()
        mock_backend.name = "ovs"
        mock_backend.initialize = AsyncMock(return_value={})
        mock_backend.shutdown = AsyncMock()
        monkeypatch.setattr("agent.main.get_network_backend", lambda: mock_backend)

        # Mock snapshotter check
        monkeypatch.setattr("agent.main._log_docker_snapshotter_mode_at_startup", AsyncMock())

        # Make Redis ping fail
        mock_lm = AsyncMock()
        mock_lm.ping = AsyncMock(side_effect=ConnectionError("no redis"))
        mock_lm.clear_agent_locks = AsyncMock(return_value=[])
        mock_lm.close = AsyncMock()

        noop_lm = MagicMock()
        noop_lm.clear_agent_locks = AsyncMock(return_value=[])
        noop_lm.close = AsyncMock()

        from agent import locks as locks_mod

        monkeypatch.setattr(locks_mod, "DeployLockManager", lambda **kw: mock_lm)
        monkeypatch.setattr(locks_mod, "NoopDeployLockManager", lambda **kw: noop_lm)
        # Patch the lifespan-scoped import targets too
        monkeypatch.setattr("agent.locks.DeployLockManager", lambda **kw: mock_lm)
        monkeypatch.setattr("agent.locks.NoopDeployLockManager", lambda **kw: noop_lm)

        # Mock cleanup manager
        mock_cleanup = AsyncMock()
        mock_cleanup.run_full_cleanup = AsyncMock(return_value=MagicMock(veths_deleted=0, bridges_deleted=0))
        mock_cleanup.start_periodic_cleanup = AsyncMock()
        mock_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.network.cleanup.get_cleanup_manager", lambda: mock_cleanup)

        # Mock image transfer state
        monkeypatch.setattr("agent.routers.images._load_persisted_transfer_state", lambda: None)

        # Mock image cleanup
        mock_img_cleanup = AsyncMock()
        mock_img_cleanup.cleanup_stale_temp_files = AsyncMock(
            return_value=MagicMock(temp_files_deleted=0, partial_files_deleted=0)
        )
        mock_img_cleanup.start_periodic_cleanup = AsyncMock()
        mock_img_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.image_cleanup.get_image_cleanup_manager", lambda *a, **kw: mock_img_cleanup)

        # Mock async detect IP
        monkeypatch.setattr(_state, "_async_detect_local_ip", AsyncMock())

        # Mock registration and bootstrap
        monkeypatch.setattr("agent.main.register_with_controller", AsyncMock())
        monkeypatch.setattr("agent.main._bootstrap_transport_config", AsyncMock())

        # Mock heartbeat
        heartbeat_task = asyncio.Future()
        heartbeat_task.set_result(None)
        monkeypatch.setattr("agent.main.heartbeat_loop", AsyncMock(return_value=None))

        # Disable docker event listener, carrier monitor, vxlan
        monkeypatch.setattr("agent.config.settings.enable_docker", False)
        monkeypatch.setattr("agent.config.settings.enable_ovs", False)
        monkeypatch.setattr("agent.config.settings.enable_vxlan", False)

        # Mock cleanup tasks
        monkeypatch.setattr("agent.main._cleanup_lingering_virsh_sessions", AsyncMock())
        monkeypatch.setattr("agent.main.close_http_client", AsyncMock())

        app = MagicMock()
        async with lifespan(app):
            # After startup, noop lock manager should have been set
            # (the original mock_lm.ping raised, so it switches to noop)
            pass

    @pytest.mark.asyncio
    async def test_orphaned_locks_cleared(self, monkeypatch):
        """Orphaned locks from previous run should be cleared on startup."""
        from agent.main import lifespan
        import agent.agent_state as _state

        monkeypatch.delenv("ARCHETYPE_AGENT_TESTING", raising=False)
        monkeypatch.setattr("agent.main.check_and_rollback", lambda: None)
        monkeypatch.setattr("agent.main.get_capabilities", lambda: [])

        mock_backend = AsyncMock()
        mock_backend.name = "ovs"
        mock_backend.initialize = AsyncMock(return_value={})
        mock_backend.shutdown = AsyncMock()
        monkeypatch.setattr("agent.main.get_network_backend", lambda: mock_backend)
        monkeypatch.setattr("agent.main._log_docker_snapshotter_mode_at_startup", AsyncMock())

        cleared_locks = ["deploy_lock:lab-1", "deploy_lock:lab-2"]
        mock_lm = AsyncMock()
        mock_lm.ping = AsyncMock()
        mock_lm.clear_agent_locks = AsyncMock(return_value=cleared_locks)
        mock_lm.close = AsyncMock()

        from agent import locks as locks_mod
        monkeypatch.setattr(locks_mod, "DeployLockManager", lambda **kw: mock_lm)
        monkeypatch.setattr(locks_mod, "set_lock_manager", lambda lm: None)

        mock_cleanup = AsyncMock()
        mock_cleanup.run_full_cleanup = AsyncMock(return_value=MagicMock(veths_deleted=0, bridges_deleted=0))
        mock_cleanup.start_periodic_cleanup = AsyncMock()
        mock_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.network.cleanup.get_cleanup_manager", lambda: mock_cleanup)

        monkeypatch.setattr("agent.routers.images._load_persisted_transfer_state", lambda: None)

        mock_img_cleanup = AsyncMock()
        mock_img_cleanup.cleanup_stale_temp_files = AsyncMock(
            return_value=MagicMock(temp_files_deleted=0, partial_files_deleted=0)
        )
        mock_img_cleanup.start_periodic_cleanup = AsyncMock()
        mock_img_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.image_cleanup.get_image_cleanup_manager", lambda *a, **kw: mock_img_cleanup)

        monkeypatch.setattr(_state, "_async_detect_local_ip", AsyncMock())
        monkeypatch.setattr("agent.main.register_with_controller", AsyncMock())
        monkeypatch.setattr("agent.main._bootstrap_transport_config", AsyncMock())
        monkeypatch.setattr("agent.main.heartbeat_loop", AsyncMock(return_value=None))
        monkeypatch.setattr("agent.config.settings.enable_docker", False)
        monkeypatch.setattr("agent.config.settings.enable_ovs", False)
        monkeypatch.setattr("agent.config.settings.enable_vxlan", False)
        monkeypatch.setattr("agent.main._cleanup_lingering_virsh_sessions", AsyncMock())
        monkeypatch.setattr("agent.main.close_http_client", AsyncMock())

        app = MagicMock()
        async with lifespan(app):
            mock_lm.clear_agent_locks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_network_backend_init_failure_non_fatal(self, monkeypatch):
        """Network backend initialization failure should not crash startup."""
        from agent.main import lifespan
        import agent.agent_state as _state

        monkeypatch.delenv("ARCHETYPE_AGENT_TESTING", raising=False)
        monkeypatch.setattr("agent.main.check_and_rollback", lambda: None)
        monkeypatch.setattr("agent.main.get_capabilities", lambda: [])

        # First call for logging, second for init — make init fail
        call_count = 0
        mock_backend = AsyncMock()
        mock_backend.name = "ovs"
        mock_backend.initialize = AsyncMock(side_effect=RuntimeError("OVS not available"))
        mock_backend.shutdown = AsyncMock()
        monkeypatch.setattr("agent.main.get_network_backend", lambda: mock_backend)
        monkeypatch.setattr("agent.main._log_docker_snapshotter_mode_at_startup", AsyncMock())

        mock_lm = AsyncMock()
        mock_lm.ping = AsyncMock()
        mock_lm.clear_agent_locks = AsyncMock(return_value=[])
        mock_lm.close = AsyncMock()

        from agent import locks as locks_mod
        monkeypatch.setattr(locks_mod, "DeployLockManager", lambda **kw: mock_lm)
        monkeypatch.setattr(locks_mod, "set_lock_manager", lambda lm: None)

        mock_cleanup = AsyncMock()
        mock_cleanup.run_full_cleanup = AsyncMock(return_value=MagicMock(veths_deleted=0, bridges_deleted=0))
        mock_cleanup.start_periodic_cleanup = AsyncMock()
        mock_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.network.cleanup.get_cleanup_manager", lambda: mock_cleanup)

        monkeypatch.setattr("agent.routers.images._load_persisted_transfer_state", lambda: None)

        mock_img_cleanup = AsyncMock()
        mock_img_cleanup.cleanup_stale_temp_files = AsyncMock(
            return_value=MagicMock(temp_files_deleted=0, partial_files_deleted=0)
        )
        mock_img_cleanup.start_periodic_cleanup = AsyncMock()
        mock_img_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.image_cleanup.get_image_cleanup_manager", lambda *a, **kw: mock_img_cleanup)

        monkeypatch.setattr(_state, "_async_detect_local_ip", AsyncMock())
        monkeypatch.setattr("agent.main.register_with_controller", AsyncMock())
        monkeypatch.setattr("agent.main._bootstrap_transport_config", AsyncMock())
        monkeypatch.setattr("agent.main.heartbeat_loop", AsyncMock(return_value=None))
        monkeypatch.setattr("agent.config.settings.enable_docker", False)
        monkeypatch.setattr("agent.config.settings.enable_ovs", False)
        monkeypatch.setattr("agent.config.settings.enable_vxlan", False)
        monkeypatch.setattr("agent.main._cleanup_lingering_virsh_sessions", AsyncMock())
        monkeypatch.setattr("agent.main.close_http_client", AsyncMock())

        app = MagicMock()
        # Should not raise despite backend init failure
        async with lifespan(app):
            pass

    @pytest.mark.asyncio
    async def test_cleanup_manager_failure_non_fatal(self, monkeypatch):
        """Cleanup manager failure during startup should not crash the agent."""
        from agent.main import lifespan
        import agent.agent_state as _state

        monkeypatch.delenv("ARCHETYPE_AGENT_TESTING", raising=False)
        monkeypatch.setattr("agent.main.check_and_rollback", lambda: None)
        monkeypatch.setattr("agent.main.get_capabilities", lambda: [])

        mock_backend = AsyncMock()
        mock_backend.name = "ovs"
        mock_backend.initialize = AsyncMock(return_value={})
        mock_backend.shutdown = AsyncMock()
        monkeypatch.setattr("agent.main.get_network_backend", lambda: mock_backend)
        monkeypatch.setattr("agent.main._log_docker_snapshotter_mode_at_startup", AsyncMock())

        mock_lm = AsyncMock()
        mock_lm.ping = AsyncMock()
        mock_lm.clear_agent_locks = AsyncMock(return_value=[])
        mock_lm.close = AsyncMock()

        from agent import locks as locks_mod
        monkeypatch.setattr(locks_mod, "DeployLockManager", lambda **kw: mock_lm)
        monkeypatch.setattr(locks_mod, "set_lock_manager", lambda lm: None)

        # Make cleanup fail
        mock_cleanup = AsyncMock()
        mock_cleanup.run_full_cleanup = AsyncMock(side_effect=RuntimeError("cleanup broken"))
        mock_cleanup.start_periodic_cleanup = AsyncMock()
        mock_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.network.cleanup.get_cleanup_manager", lambda: mock_cleanup)

        monkeypatch.setattr("agent.routers.images._load_persisted_transfer_state", lambda: None)

        mock_img_cleanup = AsyncMock()
        mock_img_cleanup.cleanup_stale_temp_files = AsyncMock(
            return_value=MagicMock(temp_files_deleted=0, partial_files_deleted=0)
        )
        mock_img_cleanup.start_periodic_cleanup = AsyncMock()
        mock_img_cleanup.stop_periodic_cleanup = AsyncMock()
        monkeypatch.setattr("agent.image_cleanup.get_image_cleanup_manager", lambda *a, **kw: mock_img_cleanup)

        monkeypatch.setattr(_state, "_async_detect_local_ip", AsyncMock())
        monkeypatch.setattr("agent.main.register_with_controller", AsyncMock())
        monkeypatch.setattr("agent.main._bootstrap_transport_config", AsyncMock())
        monkeypatch.setattr("agent.main.heartbeat_loop", AsyncMock(return_value=None))
        monkeypatch.setattr("agent.config.settings.enable_docker", False)
        monkeypatch.setattr("agent.config.settings.enable_ovs", False)
        monkeypatch.setattr("agent.config.settings.enable_vxlan", False)
        monkeypatch.setattr("agent.main._cleanup_lingering_virsh_sessions", AsyncMock())
        monkeypatch.setattr("agent.main.close_http_client", AsyncMock())

        app = MagicMock()
        async with lifespan(app):
            pass  # Should not crash


# ---------------------------------------------------------------------------
# 8. AgentAuthMiddleware
# ---------------------------------------------------------------------------


class TestAgentAuthMiddleware:
    """Tests for AgentAuthMiddleware dispatch logic."""

    @pytest.mark.asyncio
    async def test_metrics_denied_client(self, monkeypatch):
        from agent.main import AgentAuthMiddleware

        middleware = AgentAuthMiddleware(app=MagicMock())

        from agent.config import settings
        monkeypatch.setattr(settings, "metrics_allowed_cidrs", "10.0.0.0/8")
        _parse_metrics_allowlist.cache_clear()

        request = MagicMock()
        request.url.path = "/metrics"
        request.client = MagicMock()
        request.client.host = "8.8.8.8"
        request.headers = {}

        response = await middleware.dispatch(request, AsyncMock())
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_health_exempt(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/health"
        request.headers = {}

        mock_next = AsyncMock(return_value=MagicMock(status_code=200))

        response = await middleware.dispatch(request, mock_next)
        mock_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_secret_skips_auth(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/labs"
        request.headers = {}

        mock_next = AsyncMock(return_value=MagicMock(status_code=200))
        response = await middleware.dispatch(request, mock_next)
        mock_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_auth_header(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/labs"
        request.headers = {"authorization": "", "upgrade": ""}

        response = await middleware.dispatch(request, AsyncMock())
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_token(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/labs"
        request.headers = {"authorization": "Bearer wrongtoken", "upgrade": ""}

        response = await middleware.dispatch(request, AsyncMock())
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_token(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/labs"
        request.headers = {"authorization": "Bearer mysecret", "upgrade": ""}

        mock_next = AsyncMock(return_value=MagicMock(status_code=200))
        response = await middleware.dispatch(request, mock_next)
        mock_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_websocket_upgrade_skips_auth(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/console"
        request.headers = {"upgrade": "websocket", "authorization": ""}

        mock_next = AsyncMock(return_value=MagicMock(status_code=101))
        response = await middleware.dispatch(request, mock_next)
        mock_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poap_exempt(self, monkeypatch):
        from agent.main import AgentAuthMiddleware
        from agent.config import settings

        monkeypatch.setattr(settings, "controller_secret", "mysecret")

        middleware = AgentAuthMiddleware(app=MagicMock())

        request = MagicMock()
        request.url.path = "/poap/script.py"
        request.headers = {"upgrade": ""}

        mock_next = AsyncMock(return_value=MagicMock(status_code=200))
        response = await middleware.dispatch(request, mock_next)
        mock_next.assert_awaited_once()
