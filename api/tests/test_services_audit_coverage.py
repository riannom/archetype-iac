"""Tests for app.services.audit — AuditService coverage."""
from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy.orm import Session

from app import models
from app.services.audit import AuditService


class TestAuditServiceLog:
    """Tests for AuditService.log()."""

    def test_creates_audit_entry_minimal(self, test_db: Session):
        """Log with only event_type creates a valid AuditLog row."""
        AuditService.log(test_db, "login_success")
        test_db.commit()

        entries = test_db.query(models.AuditLog).all()
        assert len(entries) == 1
        assert entries[0].event_type == "login_success"
        assert entries[0].user_id is None
        assert entries[0].details_json is None

    def test_creates_audit_entry_with_all_fields(self, test_db: Session, test_user: models.User):
        """Log with all optional fields populates every column."""
        details = {"action": "password_change", "ip": "10.0.0.1"}
        AuditService.log(
            test_db,
            "user_updated",
            user_id=test_user.id,
            target_user_id=test_user.id,
            ip_address="192.168.1.100",
            details=details,
        )
        test_db.commit()

        entry = test_db.query(models.AuditLog).first()
        assert entry is not None
        assert entry.event_type == "user_updated"
        assert entry.user_id == test_user.id
        assert entry.target_user_id == test_user.id
        assert entry.ip_address == "192.168.1.100"
        assert json.loads(entry.details_json) == details

    def test_details_none_when_not_provided(self, test_db: Session):
        """When details is None, details_json should be None."""
        AuditService.log(test_db, "logout", details=None)
        test_db.commit()

        entry = test_db.query(models.AuditLog).first()
        assert entry.details_json is None

    def test_exception_swallowed_on_flush_error(self, test_db: Session, caplog):
        """When db.flush() raises, the exception is swallowed and a warning is logged."""
        with patch.object(test_db, "flush", side_effect=Exception("DB write error")):
            # Should NOT raise
            AuditService.log(test_db, "login_failed")

        # Verify warning was logged
        assert any("Failed to write audit log" in r.message for r in caplog.records)

    def test_various_event_types(self, test_db: Session):
        """Multiple event types can be logged sequentially."""
        event_types = [
            "login_success",
            "login_failed",
            "user_created",
            "user_deleted",
            "password_changed",
            "role_changed",
        ]
        for evt in event_types:
            AuditService.log(test_db, evt)
        test_db.commit()

        entries = test_db.query(models.AuditLog).all()
        assert len(entries) == len(event_types)
        stored_types = {e.event_type for e in entries}
        assert stored_types == set(event_types)

    def test_empty_details_dict_treated_as_none(self, test_db: Session):
        """An empty dict is falsy, so details_json is None."""
        AuditService.log(test_db, "test_event", details={})
        test_db.commit()

        entry = test_db.query(models.AuditLog).first()
        # Empty dict is falsy in Python, so the `if details` guard yields None
        assert entry.details_json is None
