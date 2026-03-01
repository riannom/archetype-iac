"""Health checks for stuck agent update jobs."""
from __future__ import annotations

import logging
from datetime import timedelta, timezone

from app import models
from app.config import settings
from app.db import get_session
from app.utils.time import utcnow

logger = logging.getLogger(__name__)


def check_stuck_agent_updates():
    """Find and handle AgentUpdateJob records that are stuck.

    Detects agent update jobs stuck in active states (pending, downloading,
    installing, restarting) past the configured timeout, or assigned to
    agents that have gone offline.
    """
    with get_session() as session:
        try:
            now = utcnow()
            active_statuses = ["pending", "downloading", "installing", "restarting"]

            stuck_jobs = (
                session.query(models.AgentUpdateJob)
                .filter(models.AgentUpdateJob.status.in_(active_statuses))
                .all()
            )

            if not stuck_jobs:
                return

            timeout = timedelta(seconds=settings.agent_update_timeout)

            for job in stuck_jobs:
                try:
                    # Check if target agent is offline
                    host = session.get(models.Host, job.host_id)
                    agent_offline = host and host.status != "online"

                    # Determine reference timestamp (started_at if available, else created_at)
                    ref_time = job.started_at or job.created_at
                    if ref_time.tzinfo is None:
                        ref_time = ref_time.replace(tzinfo=timezone.utc)
                    is_timed_out = (now - ref_time) > timeout

                    if agent_offline:
                        reason = f"Agent {host.name if host else job.host_id} went offline during update"
                    elif is_timed_out:
                        age_min = (now - ref_time).total_seconds() / 60
                        reason = f"Timed out after {age_min:.0f} minutes in '{job.status}' state"
                    else:
                        continue

                    logger.warning(
                        f"Detected stuck AgentUpdateJob {job.id}: status={job.status}, "
                        f"host_id={job.host_id}, reason={reason}"
                    )

                    job.status = "failed"
                    job.error_message = reason
                    job.completed_at = now
                    session.commit()

                    logger.info(f"Marked stuck AgentUpdateJob {job.id} as failed: {reason}")

                except Exception as e:
                    session.rollback()
                    logger.error(f"Error checking AgentUpdateJob {job.id}: {e}")

        except Exception as e:
            session.rollback()
            logger.error(f"Error in agent update health check: {e}")
