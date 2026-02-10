"""Agent health monitoring background task."""
from __future__ import annotations

import asyncio
import logging

from app import agent_client
from app.config import settings
from app.db import get_session

logger = logging.getLogger(__name__)


async def agent_health_monitor():
    """Background task to monitor agent health and mark stale agents as offline."""
    logger.info("Agent health monitor started")
    while True:
        try:
            await asyncio.sleep(settings.agent_health_check_interval)
            with get_session() as session:
                try:
                    marked_offline = await agent_client.update_stale_agents(session)
                    if marked_offline:
                        logger.info(f"Marked {len(marked_offline)} agent(s) as offline")
                except Exception as e:
                    logger.error(f"Error updating stale agents: {e}")
        except asyncio.CancelledError:
            logger.info("Agent health monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in agent health monitor: {e}")
