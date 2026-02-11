"""RQ worker entrypoint with Prometheus metrics export."""
from __future__ import annotations

import logging
from os import getenv

from redis import Redis
from rq import Connection, SimpleWorker, Worker

from app.config import settings
from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _start_metrics_server() -> None:
    try:
        from prometheus_client import start_http_server
        # Ensure metric families are registered in this process at startup.
        # Without this import, job metric names may not exist until first task execution.
        from app import metrics as _metrics  # noqa: F401
    except ImportError:
        logger.warning("prometheus_client not installed; worker metrics endpoint disabled")
        return

    port = int(getenv("WORKER_METRICS_PORT", "8003"))
    start_http_server(port, addr="0.0.0.0")
    logger.info("Worker metrics endpoint started on :%s/metrics", port)


def main() -> None:
    _start_metrics_server()
    redis_conn = Redis.from_url(settings.redis_url)
    # RQ's default Worker forks child processes for jobs. Prometheus counters
    # updated in those children are not visible to the parent metrics server.
    # Use SimpleWorker by default so job metrics are emitted by the same process
    # that exposes /metrics.
    worker_mode = getenv("WORKER_EXECUTION_MODE", "simple").strip().lower()
    worker_cls = SimpleWorker if worker_mode == "simple" else Worker
    logger.info("Starting worker with execution_mode=%s (%s)", worker_mode, worker_cls.__name__)
    with Connection(redis_conn):
        worker = worker_cls(["archetype"])
        worker.work()


if __name__ == "__main__":
    main()
