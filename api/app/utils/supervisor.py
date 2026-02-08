"""Supervisor wrapper for background monitor tasks.

Provides automatic restart with exponential backoff when monitor tasks
crash unexpectedly. CancelledError (clean shutdown) is always re-raised.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine, Any

logger = logging.getLogger(__name__)


async def supervised_task(
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    name: str,
    max_restarts: int = 10,
    base_backoff: float = 5.0,
    max_backoff: float = 300.0,
) -> None:
    """Run a coroutine with automatic restart on crash.

    The factory pattern (callable returning coroutine) is used because
    a coroutine object can only be awaited once - on restart we need
    a fresh coroutine.

    Args:
        coro_factory: Callable that returns a new coroutine to run
        name: Human-readable name for logging
        max_restarts: Maximum consecutive restarts before giving up
        base_backoff: Initial backoff delay in seconds
        max_backoff: Maximum backoff delay in seconds
    """
    restarts = 0
    while restarts < max_restarts:
        try:
            logger.info(f"Starting monitor: {name}")
            await coro_factory()
            # Clean exit (shouldn't happen for infinite loops, but handle gracefully)
            logger.warning(f"Monitor {name} exited cleanly, not restarting")
            return
        except asyncio.CancelledError:
            logger.info(f"Monitor {name} cancelled (clean shutdown)")
            raise  # Always propagate cancellation
        except Exception as e:
            restarts += 1
            backoff = min(base_backoff * (2 ** (restarts - 1)), max_backoff)
            logger.error(
                f"Monitor {name} crashed (attempt {restarts}/{max_restarts}): {e}",
                exc_info=True,
            )
            if restarts < max_restarts:
                logger.info(f"Restarting {name} in {backoff:.0f}s")
                await asyncio.sleep(backoff)
            # Reset restart counter on long-running success
            # (if we get here, the monitor ran long enough to crash again)

    logger.critical(
        f"Monitor {name} exceeded max restarts ({max_restarts}), giving up"
    )
