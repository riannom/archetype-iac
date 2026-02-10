"""Agent selection helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import agent_client

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app import models


async def get_online_agent_for_lab(
    database: "Session",
    lab: "models.Lab",
    required_provider: str | None = None,
) -> "models.Host | None":
    """Get an online agent for a lab (or None if none are healthy)."""
    agent = await agent_client.get_agent_for_lab(
        database,
        lab,
        required_provider=required_provider,
    )
    if agent and agent_client.is_agent_online(agent):
        return agent
    return None
