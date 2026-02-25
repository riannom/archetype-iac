from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound, APIError

from agent.config import settings
from agent.providers.base import StatusResult
from agent.providers.docker import DockerProvider
from agent.schemas import DeployNode, DeployTopology


def _run(coro):
    return asyncio.run(coro)


def _api_conflict(message: str = "already exists") -> APIError:
    response = MagicMock()
    response.status_code = 409
    return APIError(message, response=response)


def test_create_lab_networks_uses_sanitized_prefix_and_labels(monkeypatch):
    provider = DockerProvider()
    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.networks.create = MagicMock()
    provider._docker = docker_client

    lab_id = "lab$with#bad!chars-and-very-very-long-name"
    _run(provider._create_lab_networks(lab_id, max_interfaces=1))

    args, kwargs = docker_client.networks.create.call_args
    assert kwargs["name"].startswith(provider._lab_network_prefix(lab_id))
    assert kwargs["labels"]["archetype.lab_id"] == lab_id


def test_create_lab_networks_reuses_valid_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    existing = MagicMock()
    existing.attrs = {
        "Driver": "archetype-ovs",
        "Labels": {
            "archetype.lab_id": "lab1",
            "archetype.provider": "docker",
            "archetype.type": "lab-interface",
        },
        "Options": {
            "lab_id": "lab1",
            "interface_name": "eth0",
        },
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), existing]
    docker_client.networks.create.side_effect = _api_conflict()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    result = _run(provider._create_lab_networks("lab1", max_interfaces=0))

    assert result == {"eth0": "lab1-eth0"}
    assert docker_client.networks.create.call_count == 1
    existing.remove.assert_not_called()


def test_create_lab_networks_recreates_stale_unused_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    stale = MagicMock()
    stale.attrs = {
        "Driver": "bridge",
        "Labels": {},
        "Options": {},
        "Containers": {},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), stale]
    docker_client.networks.create.side_effect = [_api_conflict(), MagicMock()]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    result = _run(provider._create_lab_networks("lab1", max_interfaces=0))

    assert result == {"eth0": "lab1-eth0"}
    stale.remove.assert_called_once()
    assert docker_client.networks.create.call_count == 2


def test_create_lab_networks_refuses_delete_for_in_use_stale_network_on_409(monkeypatch):
    provider = DockerProvider()
    provider._prune_legacy_lab_networks = AsyncMock(return_value=None)

    stale_in_use = MagicMock()
    stale_in_use.attrs = {
        "Driver": "bridge",
        "Labels": {},
        "Options": {},
        "Containers": {"cid1": {"Name": "n1"}},
    }

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = [NotFound("missing"), stale_in_use]
    docker_client.networks.create.side_effect = _api_conflict()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    with pytest.raises(RuntimeError, match="active endpoints"):
        _run(provider._create_lab_networks("lab1", max_interfaces=0))

    stale_in_use.remove.assert_not_called()


@pytest.mark.asyncio
async def test_create_lab_networks_serializes_concurrent_calls_per_lab(monkeypatch):
    provider = DockerProvider()

    active_calls = 0
    max_concurrent_calls = 0

    async def _tracked_prune(_lab_id: str):
        nonlocal active_calls, max_concurrent_calls
        active_calls += 1
        max_concurrent_calls = max(max_concurrent_calls, active_calls)
        await asyncio.sleep(0.05)
        active_calls -= 1

    provider._prune_legacy_lab_networks = AsyncMock(side_effect=_tracked_prune)

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.networks.create = MagicMock()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    await asyncio.gather(
        provider._create_lab_networks("lab-lock", max_interfaces=0),
        provider._create_lab_networks("lab-lock", max_interfaces=0),
    )

    # The per-lab lock should keep this critical section non-overlapping.
    assert max_concurrent_calls == 1


def test_local_cleanup_runs_orphan_veth_cleanup(monkeypatch):
    from agent.network import local as local_mod

    called = {"count": 0}

    class _FakeCleanupMgr:
        async def cleanup_orphaned_veths(self):
            called["count"] += 1

    monkeypatch.setattr("agent.network.cleanup.NetworkCleanupManager", _FakeCleanupMgr)

    mgr = local_mod.LocalNetworkManager()
    mgr._links = {}

    _run(mgr.cleanup_lab("lab1"))
    assert called["count"] == 1


def test_recover_stale_networks_defaults_to_eth1_when_label_missing(monkeypatch):
    provider = DockerProvider()

    net_eth1 = MagicMock()
    net_eth1.name = "lab1-eth1"
    net_eth2 = MagicMock()
    net_eth2.name = "lab1-eth2"

    docker_client = MagicMock()
    docker_client.networks.list.return_value = [net_eth1, net_eth2]
    docker_client.networks.get.side_effect = lambda name: {
        "lab1-eth1": net_eth1,
        "lab1-eth2": net_eth2,
    }[name]

    provider._docker = docker_client

    container = MagicMock()
    container.name = "archetype-lab1-n1"
    container.labels = {}
    container.attrs = {
        "NetworkSettings": {
            "Networks": {
                "lab1-eth1": {},
                "lab1-eth2": {},
            }
        }
    }

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(provider._recover_stale_networks(container, "lab1"))

    assert net_eth1.connect.call_count == 1
    assert net_eth2.connect.call_count == 0


def test_deploy_fails_on_start_failure(monkeypatch, tmp_path):
    provider = DockerProvider()

    provider._recover_stale_network = AsyncMock(return_value={})
    provider._validate_images = MagicMock(return_value=[])
    provider._ensure_directories = AsyncMock(return_value=None)
    provider._create_containers = AsyncMock(return_value={"n1": MagicMock(name="c1")})
    provider._start_containers = AsyncMock(return_value=["n1"])
    provider._create_links = AsyncMock(return_value=0)
    provider._capture_container_vlans = AsyncMock(return_value=None)
    provider._wait_for_readiness = AsyncMock(return_value={})
    provider.status = AsyncMock(return_value=StatusResult(lab_exists=True, nodes=[]))

    topology = DeployTopology(
        nodes=[DeployNode(name="n1", kind="linux", interface_count=1)],
        links=[],
    )

    result = _run(provider.deploy("lab1", topology, tmp_path))
    assert result.success is False
    assert "Failed to start" in (result.error or "")


def test_destroy_cleans_networks_before_local_and_ovs(monkeypatch, tmp_path):
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_ovs = settings.enable_ovs
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True

    provider = DockerProvider()

    calls: list[str] = []

    provider._delete_lab_networks = AsyncMock(side_effect=lambda lab_id: calls.append("networks") or 0)
    provider._local_network = MagicMock()
    provider._local_network.cleanup_lab = AsyncMock(side_effect=lambda lab_id: calls.append("local") or {})
    provider._ovs_manager = MagicMock()
    provider._ovs_manager._initialized = True
    provider._ovs_manager.cleanup_lab = AsyncMock(side_effect=lambda lab_id: calls.append("ovs") or {})

    docker_client = MagicMock()
    docker_client.containers.list.return_value = []
    provider._docker = docker_client

    provider._cleanup_lab_volumes = AsyncMock(return_value=0)

    try:
        _run(provider.destroy("lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin = original_enable_ovs_plugin
        settings.enable_ovs = original_enable_ovs

    assert calls[:3] == ["networks", "local", "ovs"]
