"""Shared Docker client with connection pooling.

Avoids creating a new ``docker.DockerClient`` on every API call.
The client is lazily created on first use via :func:`functools.lru_cache`
so test environments without Docker can import this module without failure.
"""

from __future__ import annotations

import functools

import docker


@functools.lru_cache(maxsize=1)
def get_docker_client() -> docker.DockerClient:
    """Return a cached Docker client with connection pooling."""
    return docker.from_env()
