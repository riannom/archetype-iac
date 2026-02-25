"""Supervisor wrapper for background monitor tasks.

Provides automatic restart with exponential backoff when monitor tasks
crash unexpectedly. CancelledError (clean shutdown) is always re-raised.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Coroutine, Any

logger = logging.getLogger(__name__)


async def supervised_task(
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    name: str,
    max_restarts: int | None = 10,
    base_backoff: float = 5.0,
    max_backoff: float = 300.0,
    restart_on_clean_exit: bool = False,
    healthy_run_reset_seconds: float = 300.0,
) -> None:
    """Run a coroutine with automatic restart on crash.

    The factory pattern (callable returning coroutine) is used because
    a coroutine object can only be awaited once - on restart we need
    a fresh coroutine.

    Args:
        coro_factory: Callable that returns a new coroutine to run
        name: Human-readable name for logging
        max_restarts: Maximum consecutive restarts before giving up.
            Set to None for unlimited restarts.
        base_backoff: Initial backoff delay in seconds
        max_backoff: Maximum backoff delay in seconds
        restart_on_clean_exit: Restart when coroutine returns normally.
            Useful for monitor loops that should be always-on.
        healthy_run_reset_seconds: If a run lasts at least this long, reset
            the consecutive-restart counter before applying backoff.
    """
    restarts = 0
    max_restarts_label = "unlimited" if max_restarts is None else str(max_restarts)

    while True:
        run_started_at = time.monotonic()
        try:
            logger.info(f"Starting monitor: {name}")
            await coro_factory()
            run_duration = time.monotonic() - run_started_at
            if run_duration >= healthy_run_reset_seconds and restarts:
                logger.info(
                    "Monitor %s ran healthy for %.1fs; resetting restart backoff",
                    name,
                    run_duration,
                )
                restarts = 0

            if not restart_on_clean_exit:
                # Clean exit for one-shot task behavior.
                logger.warning(f"Monitor {name} exited cleanly, not restarting")
                return

            restarts += 1
            backoff = min(base_backoff * (2 ** (restarts - 1)), max_backoff)
            logger.error(
                "Monitor %s exited unexpectedly (attempt %s/%s); restarting in %.0fs",
                name,
                restarts,
                max_restarts_label,
                backoff,
            )

            if max_restarts is not None and restarts >= max_restarts:
                logger.critical(
                    f"Monitor {name} exceeded max restarts ({max_restarts}), giving up"
                )
                return

            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            logger.info(f"Monitor {name} cancelled (clean shutdown)")
            raise  # Always propagate cancellation
        except Exception as e:
            run_duration = time.monotonic() - run_started_at
            if run_duration >= healthy_run_reset_seconds and restarts:
                logger.info(
                    "Monitor %s ran healthy for %.1fs before crash; resetting restart backoff",
                    name,
                    run_duration,
                )
                restarts = 0
            restarts += 1
            backoff = min(base_backoff * (2 ** (restarts - 1)), max_backoff)
            logger.error(
                f"Monitor {name} crashed (attempt {restarts}/{max_restarts_label}): {e}",
                exc_info=True,
            )

            if max_restarts is not None and restarts >= max_restarts:
                logger.critical(
                    f"Monitor {name} exceeded max restarts ({max_restarts}), giving up"
                )
                return

            logger.info(f"Restarting {name} in {backoff:.0f}s")
            await asyncio.sleep(backoff)
