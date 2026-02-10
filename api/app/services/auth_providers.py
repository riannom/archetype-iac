"""
Auth provider abstraction for extensible authentication.

Supports local password auth and OIDC. Designed for future SAML/SSO providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app import models
from app.auth import verify_password
from app.config import settings

class AuthProvider(ABC):
    """Base class for authentication providers."""

    @abstractmethod
    def authenticate(self, db: Session, identifier: str, password: str) -> models.User | None:
        """Authenticate a user with credentials. Returns User or None."""
        ...

    @abstractmethod
    def on_external_login(self, db: Session, user_info: dict) -> models.User:
        """Handle login from an external identity provider (OIDC, SAML, etc.)."""
        ...


class LocalAuthProvider(AuthProvider):
    """Local username/password authentication."""

    def authenticate(self, db: Session, identifier: str, password: str) -> models.User | None:
        # Try username first, then email fallback
        user = db.query(models.User).filter(models.User.username == identifier.lower()).first()
        if not user:
            user = db.query(models.User).filter(models.User.email == identifier).first()
        if not user or not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user

    def on_external_login(self, db: Session, user_info: dict) -> models.User:
        raise NotImplementedError("LocalAuthProvider does not support external login")


class OIDCAuthProvider(AuthProvider):
    """OpenID Connect authentication."""

    def authenticate(self, db: Session, identifier: str, password: str) -> models.User | None:
        raise NotImplementedError("OIDC does not support password authentication")

    def on_external_login(self, db: Session, user_info: dict) -> models.User:
        """Auto-create or update user from OIDC claims."""
        email = user_info.get("email", "")
        preferred_username = user_info.get("preferred_username", "")

        # Derive username from OIDC claims
        username = preferred_username.lower().strip() if preferred_username else ""
        if not username and email:
            username = email.split("@")[0].lower()

        # Look up existing user by email
        user = db.query(models.User).filter(models.User.email == email).first()
        if user:
            return user

        # Auto-create with configured default role
        default_role = getattr(settings, "oidc_default_role", "viewer")
        import re
        import uuid
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9._-]{2,31}$", username):
            username = f"user_{uuid.uuid4().hex[:8]}"

        # Handle username collision
        base_username = username
        suffix = 2
        while db.query(models.User).filter(models.User.username == username).first():
            username = f"{base_username}_{suffix}"
            suffix += 1

        from app.auth import hash_password
        user = models.User(
            username=username,
            email=email,
            hashed_password=hash_password(uuid.uuid4().hex),
            global_role=default_role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def get_auth_provider() -> AuthProvider:
    """Get the configured auth provider."""
    return LocalAuthProvider()
