"""Callback delivery with retry logic.

This module handles delivering job completion callbacks to the controller
with exponential backoff retry and dead letter queue for persistent failures.

The callback system enables async job execution:
1. Agent accepts job and returns 202 immediately
2. Agent executes operation in background
3. Agent POSTs result to callback URL when done
4. If callback fails, retry with exponential backoff
5. After max retries, send to dead letter endpoint

This eliminates timeout issues for long-running operations.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx


logger = logging.getLogger(__name__)

# Retry configuration
DEFAULT_RETRY_DELAYS = [10, 30, 60]  # Seconds between retries
MAX_RETRY_ATTEMPTS = 3
DEAD_LETTER_TTL = 86400  # 24 hours


@dataclass
class CallbackPayload:
    """Payload for a job completion callback."""

    job_id: str
    agent_id: str
    status: str  # completed, failed
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    node_states: dict[str, str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error_message": self.error_message,
            "node_states": self.node_states,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


@dataclass
class PendingCallback:
    """A callback that's being retried."""

    callback_url: str
    payload: CallbackPayload
    attempt: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# In-memory dead letter queue
_dead_letters: list[PendingCallback] = []


async def deliver_callback(
    callback_url: str,
    payload: CallbackPayload,
    retry_delays: list[int] | None = None,
) -> bool:
    """Deliver a callback with retry logic.

    Attempts to POST the payload to the callback URL. If delivery fails,
    retries with exponential backoff. After max retries, the callback
    is sent to the dead letter queue.

    Args:
        callback_url: URL to POST the result to
        payload: Job completion payload
        retry_delays: List of delays between retries (seconds)

    Returns:
        True if callback was delivered, False if it went to dead letter
    """
    if retry_delays is None:
        retry_delays = DEFAULT_RETRY_DELAYS

    last_error = None

    for attempt in range(len(retry_delays) + 1):
        try:
            success = await _try_deliver(callback_url, payload)
            if success:
                logger.info(f"Callback delivered for job {payload.job_id}")
                return True
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"Callback delivery failed for job {payload.job_id} "
                f"(attempt {attempt + 1}): {e}"
            )

        # Wait before retry (unless this was the last attempt)
        if attempt < len(retry_delays):
            delay = retry_delays[attempt]
            logger.info(f"Retrying callback for job {payload.job_id} in {delay}s...")
            await asyncio.sleep(delay)

    # All retries exhausted - send to dead letter
    logger.error(
        f"Callback delivery failed after {len(retry_delays) + 1} attempts "
        f"for job {payload.job_id}. Sending to dead letter queue."
    )
    await send_to_dead_letter(callback_url, payload, last_error)
    return False


async def _try_deliver(callback_url: str, payload: CallbackPayload) -> bool:
    """Attempt to deliver a callback.

    Args:
        callback_url: URL to POST to
        payload: Job completion payload

    Returns:
        True if delivery succeeded (2xx response)

    Raises:
        Exception on network/HTTP errors
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            callback_url,
            json=payload.to_dict(),
            timeout=30.0,
        )

        if response.status_code >= 200 and response.status_code < 300:
            return True
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")


async def send_to_dead_letter(
    callback_url: str,
    payload: CallbackPayload,
    error: str | None = None,
) -> None:
    """Send a failed callback to the dead letter queue.

    Attempts to notify the controller via a special dead letter endpoint.
    Also stores locally for later inspection.

    Args:
        callback_url: Original callback URL that failed
        payload: The payload that couldn't be delivered
        error: Last error message
    """
    # Store locally
    pending = PendingCallback(
        callback_url=callback_url,
        payload=payload,
        attempt=MAX_RETRY_ATTEMPTS + 1,
    )
    _dead_letters.append(pending)

    # Prune old entries
    _prune_dead_letters()

    # Try to notify controller via dead letter endpoint
    # Extract base URL from callback URL
    try:
        # callback_url is like "http://controller:8000/callbacks/job/xxx"
        # dead letter endpoint is "/callbacks/dead-letter/xxx"
        parts = callback_url.rsplit("/", 1)
        if len(parts) == 2:
            dead_letter_url = f"{parts[0]}/dead-letter/{payload.job_id}"
            async with httpx.AsyncClient() as client:
                await client.post(
                    dead_letter_url,
                    json=payload.to_dict(),
                    timeout=10.0,
                )
                logger.info(f"Dead letter notification sent for job {payload.job_id}")
    except Exception as e:
        logger.error(f"Failed to send dead letter notification: {e}")


def _prune_dead_letters() -> None:
    """Remove expired entries from dead letter queue."""
    global _dead_letters
    now = datetime.now(timezone.utc)
    _dead_letters = [
        dl for dl in _dead_letters
        if (now - dl.created_at).total_seconds() < DEAD_LETTER_TTL
    ]


def get_dead_letters() -> list[dict]:
    """Get current dead letter queue contents.

    Returns list of failed callbacks for debugging/monitoring.
    """
    return [
        {
            "job_id": dl.payload.job_id,
            "callback_url": dl.callback_url,
            "status": dl.payload.status,
            "created_at": dl.created_at.isoformat(),
            "attempts": dl.attempt,
        }
        for dl in _dead_letters
    ]


async def send_heartbeat(callback_url: str, job_id: str) -> bool:
    """Send a heartbeat to the controller for a running job.

    The heartbeat URL is derived from the callback URL by appending /heartbeat.
    This proves the job is still making progress even during long operations.

    Args:
        callback_url: The original callback URL (e.g., http://host/callbacks/job/xxx)
        job_id: The job ID

    Returns:
        True if heartbeat was delivered successfully
    """
    # Derive heartbeat URL: /callbacks/job/{id} -> /callbacks/job/{id}/heartbeat
    heartbeat_url = f"{callback_url}/heartbeat"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(heartbeat_url, timeout=10.0)
            if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"Heartbeat sent for job {job_id}")
                return True
            else:
                logger.warning(f"Heartbeat failed for job {job_id}: HTTP {response.status_code}")
                return False
    except Exception as e:
        logger.warning(f"Heartbeat failed for job {job_id}: {e}")
        return False


class HeartbeatSender:
    """Background heartbeat sender for long-running operations.

    Usage:
        async with HeartbeatSender(callback_url, job_id, interval=30):
            # Long-running operation here
            await some_slow_operation()
    """

    def __init__(self, callback_url: str, job_id: str, interval: float = 30.0):
        self.callback_url = callback_url
        self.job_id = job_id
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def __aenter__(self):
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return False

    async def _heartbeat_loop(self):
        """Send heartbeats at regular intervals."""
        while self._running:
            await asyncio.sleep(self.interval)
            if self._running:
                await send_heartbeat(self.callback_url, self.job_id)


async def execute_with_callback(
    job_id: str,
    agent_id: str,
    callback_url: str,
    operation: callable,
    *args,
    **kwargs,
) -> None:
    """Execute an operation and deliver result via callback.

    This is the main entry point for async job execution.
    Runs the operation in the background and POSTs the result
    to the callback URL when complete.

    Args:
        job_id: Job identifier
        agent_id: Agent identifier
        callback_url: URL to POST result to
        operation: Async function to execute
        *args, **kwargs: Arguments for the operation
    """
    started_at = datetime.now(timezone.utc)
    payload = CallbackPayload(
        job_id=job_id,
        agent_id=agent_id,
        status="failed",  # Default to failed, update on success
        started_at=started_at,
    )

    try:
        result = await operation(*args, **kwargs)
        payload.completed_at = datetime.now(timezone.utc)

        # Parse result from provider
        if result.success:
            payload.status = "completed"
        else:
            payload.status = "failed"
            payload.error_message = result.error

        payload.stdout = result.stdout or ""
        payload.stderr = result.stderr or ""

    except Exception as e:
        logger.exception(f"Operation failed for job {job_id}: {e}")
        payload.status = "failed"
        payload.error_message = str(e)
        payload.completed_at = datetime.now(timezone.utc)

    # Deliver callback
    await deliver_callback(callback_url, payload)
