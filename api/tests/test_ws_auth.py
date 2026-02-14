"""Tests for validate_ws_token from api/app/auth.py."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from jose import jwt
from sqlalchemy.orm import Session

from app import db, models
from app.auth import validate_ws_token
from app.config import settings


def _make_token(subject: str, expired: bool = False) -> str:
    """Create a JWT for testing."""
    if expired:
        exp = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode(
        {"sub": subject, "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


JWT_SECRET = "test-ws-jwt-secret"


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch):
    """Ensure jwt_secret is set for all tests in this module."""
    monkeypatch.setattr(settings, "jwt_secret", JWT_SECRET)


class TestValidateWsToken:
    """Unit tests for validate_ws_token()."""

    def test_valid_jwt(self, test_db: Session, test_user: models.User, monkeypatch):
        @contextmanager
        def _session():
            yield test_db

        monkeypatch.setattr(db, "get_session", _session)
        token = _make_token(test_user.id)
        user = validate_ws_token(token)
        assert user is not None
        assert user.id == test_user.id

    def test_expired_jwt(self, test_db: Session, test_user: models.User, monkeypatch):
        @contextmanager
        def _session():
            yield test_db

        monkeypatch.setattr(db, "get_session", _session)
        token = _make_token(test_user.id, expired=True)
        assert validate_ws_token(token) is None

    def test_invalid_string(self, monkeypatch):
        @contextmanager
        def _session():
            yield MagicMock()

        monkeypatch.setattr(db, "get_session", _session)
        assert validate_ws_token("garbage") is None

    def test_none_token(self):
        assert validate_ws_token(None) is None

    def test_inactive_user(self, test_db: Session, monkeypatch):
        @contextmanager
        def _session():
            yield test_db

        monkeypatch.setattr(db, "get_session", _session)

        from app.auth import hash_password

        inactive = models.User(
            username="inactive",
            email="inactive@example.com",
            hashed_password=hash_password("pass"),
            is_active=False,
            global_role="operator",
        )
        test_db.add(inactive)
        test_db.commit()
        test_db.refresh(inactive)

        token = _make_token(inactive.id)
        assert validate_ws_token(token) is None
