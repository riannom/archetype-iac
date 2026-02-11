"""RQ worker entrypoint with Prometheus metrics export."""
from __future__ import annotations

import logging
from os import getenv

from redis import Redis
from rq import Connection, Worker

from app.config import settings
from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _start_metrics_server() -> None:
    try:
        from prometheus_client import start_http_server
    except ImportError:
        logger.warning("prometheus_client not installed; worker metrics endpoint disabled")
        return

    port = int(getenv("WORKER_METRICS_PORT", "8003"))
    start_http_server(port, addr="0.0.0.0")
    logger.info("Worker metrics endpoint started on :%s/metrics", port)


def main() -> None:
    _start_metrics_server()
    redis_conn = Redis.from_url(settings.redis_url)
    with Connection(redis_conn):
        worker = Worker(["archetype"])
        worker.work()


if __name__ == "__main__":
    main()
