"""Tests for app/services/resource_monitor.py - Resource pressure monitoring."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.resource_monitor import PressureLevel, ResourceMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _disk_usage(total: int, used: int):
    """Return a shutil.disk_usage-style named tuple."""
    from collections import namedtuple
    Usage = namedtuple("usage", ["total", "used", "free"])
    return Usage(total=total, used=used, free=total - used)


def _make_agent(resource_usage=None):
    """Create a minimal agent-like object with resource_usage attribute."""
    agent = MagicMock()
    agent.resource_usage = resource_usage
    return agent


def _rusage(ru_maxrss_kb: int):
    """Return a fake resource.getrusage result with the given ru_maxrss in KB."""
    result = MagicMock()
    result.ru_maxrss = ru_maxrss_kb
    return result


# ---------------------------------------------------------------------------
# Disk pressure
# ---------------------------------------------------------------------------

class TestCheckDiskPressure:
    """Tests for ResourceMonitor.check_disk_pressure()."""

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_normal_below_warning_threshold(self, mock_settings, mock_disk_usage):
        """Disk usage below warning threshold returns NORMAL."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # 50% usage
        mock_disk_usage.return_value = _disk_usage(total=1000, used=500)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.NORMAL
        mock_disk_usage.assert_called_once_with("/var/lib/archetype")

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_warning_at_exact_threshold(self, mock_settings, mock_disk_usage):
        """Disk usage at exactly the warning threshold returns WARNING."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # Exactly 75%
        mock_disk_usage.return_value = _disk_usage(total=1000, used=750)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_warning_between_thresholds(self, mock_settings, mock_disk_usage):
        """Disk usage between warning and critical returns WARNING."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # 80% usage
        mock_disk_usage.return_value = _disk_usage(total=1000, used=800)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_critical_at_exact_threshold(self, mock_settings, mock_disk_usage):
        """Disk usage at exactly the critical threshold returns CRITICAL."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # Exactly 85%
        mock_disk_usage.return_value = _disk_usage(total=1000, used=850)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_critical_above_threshold(self, mock_settings, mock_disk_usage):
        """Disk usage above critical threshold returns CRITICAL."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # 95% usage
        mock_disk_usage.return_value = _disk_usage(total=1000, used=950)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_custom_path(self, mock_settings, mock_disk_usage):
        """Custom path is used instead of settings.workspace."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_disk_usage.return_value = _disk_usage(total=1000, used=500)

        result = ResourceMonitor.check_disk_pressure(path="/mnt/data")

        assert result == PressureLevel.NORMAL
        mock_disk_usage.assert_called_once_with("/mnt/data")

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_custom_path_as_pathlib(self, mock_settings, mock_disk_usage):
        """Custom path as pathlib.Path is converted to string."""
        from pathlib import Path
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_disk_usage.return_value = _disk_usage(total=1000, used=500)

        result = ResourceMonitor.check_disk_pressure(path=Path("/mnt/data"))

        assert result == PressureLevel.NORMAL
        mock_disk_usage.assert_called_once_with("/mnt/data")

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_returns_normal_on_error(self, mock_settings, mock_disk_usage):
        """Returns NORMAL when disk_usage raises an exception (e.g., path doesn't exist)."""
        mock_settings.workspace = "/nonexistent/path"
        mock_disk_usage.side_effect = FileNotFoundError("No such file or directory")

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_returns_normal_on_permission_error(self, mock_settings, mock_disk_usage):
        """Returns NORMAL when disk_usage raises a permission error."""
        mock_settings.workspace = "/root/secret"
        mock_disk_usage.side_effect = PermissionError("Permission denied")

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_zero_total_returns_normal(self, mock_settings, mock_disk_usage):
        """Zero total disk space returns NORMAL (avoids division by zero)."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_disk_usage.return_value = _disk_usage(total=0, used=0)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_boundary_just_below_warning(self, mock_settings, mock_disk_usage):
        """Disk usage just below warning threshold returns NORMAL."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # 74.9% usage
        mock_disk_usage.return_value = _disk_usage(total=10000, used=7490)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    def test_boundary_just_below_critical(self, mock_settings, mock_disk_usage):
        """Disk usage just below critical threshold returns WARNING."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        # 84.9% usage
        mock_disk_usage.return_value = _disk_usage(total=10000, used=8490)

        result = ResourceMonitor.check_disk_pressure()

        assert result == PressureLevel.WARNING


# ---------------------------------------------------------------------------
# Agent disk pressure
# ---------------------------------------------------------------------------

class TestCheckAgentDiskPressure:
    """Tests for ResourceMonitor.check_agent_disk_pressure()."""

    @patch("app.services.resource_monitor.settings")
    def test_normal_below_warning(self, mock_settings):
        """Agent disk percent below warning returns NORMAL."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent(json.dumps({"disk_percent": 50.0}))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.settings")
    def test_warning_at_threshold(self, mock_settings):
        """Agent disk percent at warning threshold returns WARNING."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent(json.dumps({"disk_percent": 75.0}))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.settings")
    def test_critical_at_threshold(self, mock_settings):
        """Agent disk percent at critical threshold returns CRITICAL."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent(json.dumps({"disk_percent": 85.0}))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.settings")
    def test_critical_above_threshold(self, mock_settings):
        """Agent disk percent above critical returns CRITICAL."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent(json.dumps({"disk_percent": 95.0}))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.settings")
    def test_dict_resource_usage(self, mock_settings):
        """Agent with resource_usage as a dict (not JSON string) works."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent({"disk_percent": 80.0})

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.WARNING

    def test_none_resource_usage(self):
        """Agent with None resource_usage returns NORMAL."""
        agent = _make_agent(None)

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL

    def test_empty_string_resource_usage(self):
        """Agent with empty string resource_usage returns NORMAL."""
        agent = _make_agent("")

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.settings")
    def test_missing_disk_percent_key(self, mock_settings):
        """Agent resource_usage without disk_percent key returns NORMAL (defaults to 0)."""
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        agent = _make_agent(json.dumps({"cpu_percent": 50.0}))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL

    def test_invalid_json_resource_usage(self):
        """Agent with invalid JSON resource_usage returns NORMAL."""
        agent = _make_agent("not valid json {{{")

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL

    def test_resource_usage_is_not_dict(self):
        """Agent with non-dict JSON (e.g., a list) returns NORMAL."""
        agent = _make_agent(json.dumps([1, 2, 3]))

        result = ResourceMonitor.check_agent_disk_pressure(agent)

        assert result == PressureLevel.NORMAL


# ---------------------------------------------------------------------------
# Pool pressure
# ---------------------------------------------------------------------------

class TestCheckPoolPressure:
    """Tests for ResourceMonitor.check_pool_pressure()."""

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_normal_low_usage(self, mock_db, mock_settings):
        """Low pool usage returns NORMAL."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 3  # 3/30 = 10%
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_warning_at_threshold(self, mock_db, mock_settings):
        """Pool usage at warning threshold returns WARNING."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 21  # 21/30 = 70%
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_warning_between_thresholds(self, mock_db, mock_settings):
        """Pool usage between warning and critical returns WARNING."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 24  # 24/30 = 80%
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_critical_at_threshold(self, mock_db, mock_settings):
        """Pool usage at critical threshold returns CRITICAL."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 27  # 27/30 = 90%
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_critical_fully_saturated(self, mock_db, mock_settings):
        """Fully saturated pool returns CRITICAL."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 30  # 30/30 = 100%
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_zero_capacity_returns_normal(self, mock_db, mock_settings):
        """Zero pool capacity returns NORMAL (avoids division by zero)."""
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 0

        mock_pool = MagicMock()
        mock_pool.size.return_value = 0
        mock_db.engine.pool = mock_pool

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.db")
    def test_returns_normal_when_db_unavailable(self, mock_db):
        """Returns NORMAL when the database engine is unavailable."""
        mock_db.engine.pool.size.side_effect = Exception("DB connection failed")

        result = ResourceMonitor.check_pool_pressure()

        assert result == PressureLevel.NORMAL

    def test_returns_normal_when_db_import_fails(self):
        """Returns NORMAL when app.db cannot be imported."""
        with patch.dict("sys.modules", {"app.db": None}):
            # Importing None module raises TypeError/ImportError
            # The except clause in check_pool_pressure catches all exceptions
            result = ResourceMonitor.check_pool_pressure()

            assert result == PressureLevel.NORMAL


# ---------------------------------------------------------------------------
# Memory pressure
# ---------------------------------------------------------------------------

class TestCheckMemoryPressure:
    """Tests for ResourceMonitor.check_memory_pressure()."""

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_normal_low_memory(self, mock_settings, mock_getrusage):
        """Low memory usage returns NORMAL."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # 256 MB = 256 * 1024 KB
        mock_getrusage.return_value = _rusage(256 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_warning_at_threshold(self, mock_settings, mock_getrusage):
        """Memory usage at warning threshold returns WARNING."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # Exactly 1024 MB = 1024 * 1024 KB
        mock_getrusage.return_value = _rusage(1024 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_warning_between_thresholds(self, mock_settings, mock_getrusage):
        """Memory usage between warning and critical returns WARNING."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # 1500 MB
        mock_getrusage.return_value = _rusage(1500 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.WARNING

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_critical_at_threshold(self, mock_settings, mock_getrusage):
        """Memory usage at critical threshold returns CRITICAL."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # Exactly 2048 MB
        mock_getrusage.return_value = _rusage(2048 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_critical_above_threshold(self, mock_settings, mock_getrusage):
        """Memory usage above critical threshold returns CRITICAL."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # 4096 MB
        mock_getrusage.return_value = _rusage(4096 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.CRITICAL

    @patch("app.services.resource_monitor.resource.getrusage")
    def test_returns_normal_on_error(self, mock_getrusage):
        """Returns NORMAL when getrusage raises an exception."""
        mock_getrusage.side_effect = OSError("resource info unavailable")

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.NORMAL

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.settings")
    def test_boundary_just_below_warning(self, mock_settings, mock_getrusage):
        """Memory just below warning threshold returns NORMAL."""
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        # 1023 MB = 1023 * 1024 KB
        mock_getrusage.return_value = _rusage(1023 * 1024)

        result = ResourceMonitor.check_memory_pressure()

        assert result == PressureLevel.NORMAL


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------

class TestGetStatus:
    """Tests for ResourceMonitor.get_status()."""

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_returns_complete_structure(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() returns dict with disk, memory, and db_pool sections."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # Disk: 50% usage -> normal
        mock_disk_usage.return_value = _disk_usage(total=100 * (1024 ** 3), used=50 * (1024 ** 3))

        # Memory: 256 MB -> normal
        mock_getrusage.return_value = _rusage(256 * 1024)

        # Pool: 3 checked out of 30 capacity -> normal
        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 3
        mock_pool.checkedin.return_value = 7
        mock_pool.overflow.return_value = 0
        mock_db.engine.pool = mock_pool

        status = ResourceMonitor.get_status()

        # Verify top-level keys
        assert "disk" in status
        assert "memory" in status
        assert "db_pool" in status

        # Verify disk section
        assert status["disk"]["pressure"] == "normal"
        assert "total_gb" in status["disk"]
        assert "used_gb" in status["disk"]
        assert "free_gb" in status["disk"]
        assert "percent" in status["disk"]
        assert status["disk"]["total_gb"] == 100.0
        assert status["disk"]["used_gb"] == 50.0
        assert status["disk"]["free_gb"] == 50.0
        assert status["disk"]["percent"] == 50.0

        # Verify memory section
        assert status["memory"]["pressure"] == "normal"
        assert "rss_mb" in status["memory"]
        assert status["memory"]["rss_mb"] == 256.0

        # Verify db_pool section
        assert status["db_pool"]["pressure"] == "normal"
        assert status["db_pool"]["size"] == 10
        assert status["db_pool"]["checked_in"] == 7
        assert status["db_pool"]["checked_out"] == 3
        assert status["db_pool"]["overflow"] == 0

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_pressure_levels_reflected_in_status(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() reflects correct pressure levels for each resource."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # Disk: 80% -> warning
        mock_disk_usage.return_value = _disk_usage(total=1000, used=800)
        # Memory: 2048 MB -> critical
        mock_getrusage.return_value = _rusage(2048 * 1024)
        # Pool: 27/30 = 90% -> critical
        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 27
        mock_pool.checkedin.return_value = 3
        mock_pool.overflow.return_value = 17
        mock_db.engine.pool = mock_pool

        status = ResourceMonitor.get_status()

        assert status["disk"]["pressure"] == "warning"
        assert status["memory"]["pressure"] == "critical"
        assert status["db_pool"]["pressure"] == "critical"

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_handles_disk_error_gracefully(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() still returns partial data when disk check fails."""
        mock_settings.workspace = "/nonexistent"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # Disk fails
        mock_disk_usage.side_effect = FileNotFoundError("no such path")
        # Memory OK
        mock_getrusage.return_value = _rusage(256 * 1024)
        # Pool OK
        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 3
        mock_pool.checkedin.return_value = 7
        mock_pool.overflow.return_value = 0
        mock_db.engine.pool = mock_pool

        status = ResourceMonitor.get_status()

        # Disk section should have pressure but no metrics
        assert status["disk"]["pressure"] == "normal"
        assert "total_gb" not in status["disk"]

        # Other sections should be populated normally
        assert status["memory"]["pressure"] == "normal"
        assert "rss_mb" in status["memory"]
        assert status["db_pool"]["pressure"] == "normal"
        assert "size" in status["db_pool"]

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_handles_memory_error_gracefully(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() still returns partial data when memory check fails."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # Disk OK
        mock_disk_usage.return_value = _disk_usage(total=1000, used=500)
        # Memory fails
        mock_getrusage.side_effect = OSError("resource unavailable")
        # Pool OK
        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 3
        mock_pool.checkedin.return_value = 7
        mock_pool.overflow.return_value = 0
        mock_db.engine.pool = mock_pool

        status = ResourceMonitor.get_status()

        # Disk populated
        assert status["disk"]["pressure"] == "normal"
        assert "total_gb" in status["disk"]

        # Memory section should have pressure but no metrics
        assert status["memory"]["pressure"] == "normal"
        assert "rss_mb" not in status["memory"]

        # Pool populated
        assert status["db_pool"]["pressure"] == "normal"
        assert "size" in status["db_pool"]

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_handles_pool_error_gracefully(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() still returns partial data when pool check fails."""
        mock_settings.workspace = "/var/lib/archetype"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # Disk OK
        mock_disk_usage.return_value = _disk_usage(total=1000, used=500)
        # Memory OK
        mock_getrusage.return_value = _rusage(256 * 1024)
        # Pool fails
        mock_db.engine.pool.size.side_effect = Exception("DB down")

        status = ResourceMonitor.get_status()

        # Disk and memory populated
        assert status["disk"]["pressure"] == "normal"
        assert "total_gb" in status["disk"]
        assert status["memory"]["pressure"] == "normal"
        assert "rss_mb" in status["memory"]

        # Pool section should have pressure but no metrics
        assert status["db_pool"]["pressure"] == "normal"
        assert "size" not in status["db_pool"]

    @patch("app.services.resource_monitor.resource.getrusage")
    @patch("app.services.resource_monitor.shutil.disk_usage")
    @patch("app.services.resource_monitor.settings")
    @patch("app.db")
    def test_handles_all_errors_gracefully(
        self, mock_db, mock_settings, mock_disk_usage, mock_getrusage
    ):
        """get_status() returns valid structure even when all checks fail."""
        mock_settings.workspace = "/nonexistent"
        mock_settings.cleanup_disk_warning_pct = 75
        mock_settings.cleanup_disk_critical_pct = 85
        mock_settings.process_memory_warning_mb = 1024
        mock_settings.process_memory_critical_mb = 2048
        mock_settings.db_pool_warning_pct = 70
        mock_settings.db_pool_critical_pct = 90
        mock_settings.db_max_overflow = 20

        # All fail
        mock_disk_usage.side_effect = FileNotFoundError("no path")
        mock_getrusage.side_effect = OSError("no resource info")
        mock_db.engine.pool.size.side_effect = Exception("DB down")

        status = ResourceMonitor.get_status()

        # All sections exist with pressure=normal but no metrics
        assert status["disk"]["pressure"] == "normal"
        assert status["memory"]["pressure"] == "normal"
        assert status["db_pool"]["pressure"] == "normal"

        # No metric keys in any section
        assert "total_gb" not in status["disk"]
        assert "rss_mb" not in status["memory"]
        assert "size" not in status["db_pool"]


# ---------------------------------------------------------------------------
# PressureLevel enum
# ---------------------------------------------------------------------------

class TestPressureLevel:
    """Tests for the PressureLevel enum."""

    def test_is_string_enum(self):
        """PressureLevel values are strings."""
        assert PressureLevel.NORMAL == "normal"
        assert PressureLevel.WARNING == "warning"
        assert PressureLevel.CRITICAL == "critical"

    def test_values_are_lowercase(self):
        """PressureLevel values are lowercase."""
        for level in PressureLevel:
            assert level.value == level.value.lower()

    def test_has_three_members(self):
        """PressureLevel has exactly three members."""
        assert len(PressureLevel) == 3
