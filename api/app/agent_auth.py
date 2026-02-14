"""Agent authentication dependency for controller endpoints."""
from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)


async def verify_agent_secret(request: Request) -> None:
    """Validate pre-shared agent secret on inbound agent requests.

    If agent_secret is empty, validation is skipped (backward compatibility).
    """
    if not settings.agent_secret:
        return

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing agent authorization",
        )

    token = auth.split(" ", 1)[1]
    if not hmac.compare_digest(token, settings.agent_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid agent authorization",
        )
