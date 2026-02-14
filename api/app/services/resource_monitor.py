"""Resource pressure monitoring for the API process.

Checks disk, memory, DB pool, and agent disk pressure levels.
Used by health endpoints and cleanup tasks to decide urgency.
"""
from __future__ import annotations

import json
import logging
import resource
import shutil
from enum import Enum
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class PressureLevel(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class ResourceMonitor:
    """Lightweight resource pressure checks for the API process."""

    @staticmethod
    def check_disk_pressure(path: str | Path | None = None) -> PressureLevel:
        """Check local disk pressure using shutil.disk_usage().

        Args:
            path: Filesystem path to check. Defaults to settings.workspace.

        Returns:
            PressureLevel based on configured thresholds.
        """
        try:
            check_path = str(path) if path else settings.workspace
            usage = shutil.disk_usage(check_path)
            if usage.total == 0:
                return PressureLevel.NORMAL
            pct = (usage.used / usage.total) * 100
            if pct >= settings.cleanup_disk_critical_pct:
                return PressureLevel.CRITICAL
            if pct >= settings.cleanup_disk_warning_pct:
                return PressureLevel.WARNING
            return PressureLevel.NORMAL
        except Exception:
            logger.debug("Failed to check disk pressure", exc_info=True)
            return PressureLevel.NORMAL

    @staticmethod
    def check_agent_disk_pressure(agent) -> PressureLevel:
        """Check an agent's disk pressure from its heartbeat resource_usage JSON.

        Args:
            agent: A Host model instance with a resource_usage JSON text field.

        Returns:
            PressureLevel based on disk_percent in resource_usage.
        """
        try:
            raw = agent.resource_usage
            if not raw:
                return PressureLevel.NORMAL
            data = json.loads(raw) if isinstance(raw, str) else raw
            disk_pct = data.get("disk_percent", 0)
            if disk_pct >= settings.cleanup_disk_critical_pct:
                return PressureLevel.CRITICAL
            if disk_pct >= settings.cleanup_disk_warning_pct:
                return PressureLevel.WARNING
            return PressureLevel.NORMAL
        except Exception:
            logger.debug("Failed to check agent disk pressure", exc_info=True)
            return PressureLevel.NORMAL

    @staticmethod
    def check_pool_pressure() -> PressureLevel:
        """Check database connection pool pressure.

        Uses engine.pool stats: checked_out / (size + overflow capacity).

        Returns:
            PressureLevel based on configured thresholds.
        """
        try:
            from app import db
            pool = db.engine.pool
            pool_size = pool.size()
            # Total capacity is base size + max overflow
            max_overflow = settings.db_max_overflow
            total_capacity = pool_size + max_overflow
            if total_capacity == 0:
                return PressureLevel.NORMAL
            checked_out = pool.checkedout()
            pct = (checked_out / total_capacity) * 100
            if pct >= settings.db_pool_critical_pct:
                return PressureLevel.CRITICAL
            if pct >= settings.db_pool_warning_pct:
                return PressureLevel.WARNING
            return PressureLevel.NORMAL
        except Exception:
            logger.debug("Failed to check pool pressure", exc_info=True)
            return PressureLevel.NORMAL

    @staticmethod
    def check_memory_pressure() -> PressureLevel:
        """Check API process memory pressure via getrusage RSS.

        On Linux, ru_maxrss is in KB. Converts to MB for threshold comparison.

        Returns:
            PressureLevel based on configured thresholds.
        """
        try:
            rusage = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is in KB on Linux
            rss_mb = rusage.ru_maxrss / 1024
            if rss_mb >= settings.process_memory_critical_mb:
                return PressureLevel.CRITICAL
            if rss_mb >= settings.process_memory_warning_mb:
                return PressureLevel.WARNING
            return PressureLevel.NORMAL
        except Exception:
            logger.debug("Failed to check memory pressure", exc_info=True)
            return PressureLevel.NORMAL

    @classmethod
    def get_status(cls) -> dict:
        """Return combined pressure levels and raw metrics for all resources.

        Returns:
            Dict with pressure levels and raw metric values.
        """
        # Disk
        disk_pressure = cls.check_disk_pressure()
        disk_metrics = {}
        try:
            usage = shutil.disk_usage(settings.workspace)
            disk_metrics = {
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "percent": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0,
            }
        except Exception:
            pass

        # Memory
        memory_pressure = cls.check_memory_pressure()
        memory_metrics = {}
        try:
            rusage = resource.getrusage(resource.RUSAGE_SELF)
            memory_metrics = {
                "rss_mb": round(rusage.ru_maxrss / 1024, 1),
            }
        except Exception:
            pass

        # DB pool
        pool_pressure = cls.check_pool_pressure()
        pool_metrics = {}
        try:
            from app import db
            pool = db.engine.pool
            pool_metrics = {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            }
        except Exception:
            pass

        return {
            "disk": {
                "pressure": disk_pressure.value,
                **disk_metrics,
            },
            "memory": {
                "pressure": memory_pressure.value,
                **memory_metrics,
            },
            "db_pool": {
                "pressure": pool_pressure.value,
                **pool_metrics,
            },
        }
