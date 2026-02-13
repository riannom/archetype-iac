from __future__ import annotations

import asyncio


def test_docker_event_listener_lazily_initializes_stop_event() -> None:
    """Regression test for Python 3.11+: don't bind asyncio.Event in __init__."""
    from agent.events.docker_events import DockerEventListener

    listener = DockerEventListener()
    assert listener._stop_event is None


def test_docker_ovs_plugin_lazily_initializes_lock() -> None:
    """Regression test for Python 3.11+: don't bind asyncio.Lock in __init__."""
    from agent.network.docker_plugin import DockerOVSPlugin

    plugin = DockerOVSPlugin()
    assert plugin._lock is None

    async def _touch_lock() -> None:
        async with plugin._locked():
            return None

    asyncio.run(_touch_lock())
    assert plugin._lock is not None

