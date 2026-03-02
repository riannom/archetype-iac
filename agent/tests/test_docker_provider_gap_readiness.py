from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.providers import docker as docker_mod
from agent.providers.docker import DockerProvider, ParsedTopology, TopologyNode


@pytest.fixture
def fast_async(monkeypatch):
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def _sleep(_seconds: float):
        return None

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr(asyncio, "sleep", _sleep)


def _container(name: str, *, status: str = "running", logs: bytes = b""):
    return SimpleNamespace(
        name=name,
        status=status,
        reload=MagicMock(),
        logs=MagicMock(return_value=logs),
    )


@pytest.mark.asyncio
async def test_wait_for_readiness_marks_missing_and_none_probe_ready(fast_async):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(name="n1", kind="linux", readiness_probe="none"),
        },
        links=[],
    )
    containers = {
        "n1": _container("ctr-n1"),
        "ghost": _container("ctr-ghost"),
    }

    result = await provider._wait_for_readiness(topology, "lab", containers, timeout=0.1)

    assert result == {"n1": True, "ghost": True}


@pytest.mark.asyncio
async def test_wait_for_readiness_matches_log_pattern_and_runs_post_boot(fast_async):
    provider = DockerProvider()
    provider._run_post_boot_commands = AsyncMock()

    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(
                name="n1",
                kind="ceos",
                readiness_probe="log_pattern",
                readiness_pattern="READY",
                readiness_timeout=30,
            ),
        },
        links=[],
    )
    containers = {"n1": _container("ctr-n1", logs=b"... boot ... READY ...")}

    result = await provider._wait_for_readiness(topology, "lab", containers, timeout=0.1)

    assert result["n1"] is True
    provider._run_post_boot_commands.assert_awaited_once_with("ctr-n1", "ceos")


@pytest.mark.asyncio
async def test_wait_for_readiness_marks_ready_for_non_log_probe(fast_async):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(
                name="n1",
                kind="linux",
                readiness_probe="http",
                readiness_timeout=30,
            ),
        },
        links=[],
    )
    containers = {"n1": _container("ctr-n1")}

    result = await provider._wait_for_readiness(topology, "lab", containers, timeout=0.1)

    assert result["n1"] is True


@pytest.mark.asyncio
async def test_wait_for_readiness_retries_when_container_not_running(fast_async):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(
                name="n1",
                kind="linux",
                readiness_probe="log_pattern",
                readiness_pattern="READY",
                readiness_timeout=120,
            ),
        },
        links=[],
    )
    containers = {"n1": _container("ctr-n1", status="exited")}

    result = await provider._wait_for_readiness(topology, "lab", containers, timeout=0.002)

    assert result["n1"] is False


@pytest.mark.asyncio
async def test_wait_for_readiness_marks_not_ready_when_pattern_missing(fast_async):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(
                name="n1",
                kind="linux",
                readiness_probe="log_pattern",
                readiness_pattern="READY",
                readiness_timeout=120,
            ),
        },
        links=[],
    )
    containers = {"n1": _container("ctr-n1", logs=b"... boot ... not yet ...")}

    result = await provider._wait_for_readiness(topology, "lab", containers, timeout=0.002)

    assert result["n1"] is False


@pytest.mark.asyncio
async def test_wait_for_readiness_uses_node_timeout_and_log_error_path(fast_async, monkeypatch):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "n_timeout": TopologyNode(name="n_timeout", kind="k_timeout"),
            "n_error": TopologyNode(name="n_error", kind="k_error"),
        },
        links=[],
    )

    timeout_ctr = _container("ctr-timeout")
    error_ctr = _container("ctr-error")
    error_ctr.logs.side_effect = RuntimeError("logs unavailable")

    def _config(kind: str):
        if kind == "k_timeout":
            return SimpleNamespace(readiness_probe="log_pattern", readiness_pattern="READY", readiness_timeout=0)
        return SimpleNamespace(readiness_probe="log_pattern", readiness_pattern="READY", readiness_timeout=120)

    monkeypatch.setattr(docker_mod, "get_config_by_device", _config)

    result = await provider._wait_for_readiness(
        topology,
        "lab",
        {"n_timeout": timeout_ctr, "n_error": error_ctr},
        timeout=0.002,
    )

    assert result["n_timeout"] is False
    assert result["n_error"] is False


@pytest.mark.asyncio
async def test_run_post_boot_commands_swallow_errors():
    provider = DockerProvider()

    with pytest.MonkeyPatch.context() as mp:
        from agent import readiness as readiness_mod

        run_cmds = AsyncMock(side_effect=RuntimeError("failed"))
        mp.setattr(readiness_mod, "run_post_boot_commands", run_cmds)

        await provider._run_post_boot_commands("ctr-n1", "ceos")

    run_cmds.assert_awaited_once_with("ctr-n1", "ceos")

