"""Audit logging service for tracking auth and user management events."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)


class AuditService:
    """Records audit log entries for security-relevant events."""

    @staticmethod
    def log(
        db: Session,
        event_type: str,
        *,
        user_id: str | None = None,
        target_user_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Create an audit log entry.

        Args:
            db: Database session
            event_type: Event type (e.g., "login_success", "user_created")
            user_id: ID of the user performing the action
            target_user_id: ID of the user being acted upon
            ip_address: Client IP address
            details: Additional event details as a dict
        """
        entry = models.AuditLog(
            event_type=event_type,
            user_id=user_id,
            target_user_id=target_user_id,
            ip_address=ip_address,
            details_json=json.dumps(details) if details else None,
        )
        db.add(entry)
        try:
            db.flush()
        except Exception:
            logger.warning(f"Failed to write audit log: {event_type}", exc_info=True)
