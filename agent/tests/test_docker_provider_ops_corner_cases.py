from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound

from agent.config import settings
from agent.providers.base import StatusResult
from agent.providers.docker import DockerProvider
from agent.schemas import DeployNode, DeployTopology


def _run(coro):
    return asyncio.run(coro)


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


def test_deploy_uses_plugin_mgmt_when_enabled(monkeypatch, tmp_path):
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_ovs = settings.enable_ovs
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True

    provider = DockerProvider()

    plugin = MagicMock()
    plugin.create_management_network = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        "agent.providers.docker.get_docker_ovs_plugin",
        lambda: plugin,
    )

    provider.local_network.create_management_network = AsyncMock(
        side_effect=AssertionError("local management network should not be called")
    )

    provider._recover_stale_network = AsyncMock(return_value={})
    provider._validate_images = MagicMock(return_value=[])
    provider._ensure_directories = AsyncMock(return_value=None)
    provider._create_containers = AsyncMock(return_value={})
    provider._start_containers = AsyncMock(return_value=[])
    provider._create_links = AsyncMock(return_value=0)
    provider._capture_container_vlans = AsyncMock(return_value=None)
    provider._wait_for_readiness = AsyncMock(return_value={})
    provider.status = AsyncMock(return_value=StatusResult(lab_exists=True, nodes=[]))

    topology = DeployTopology(
        nodes=[DeployNode(name="n1", kind="linux", interface_count=1)],
        links=[],
    )

    try:
        _run(provider.deploy("lab1", topology, tmp_path))
    finally:
        settings.enable_ovs_plugin = original_enable_ovs_plugin
        settings.enable_ovs = original_enable_ovs

    plugin.create_management_network.assert_awaited_once_with("lab1")


def test_local_cleanup_runs_orphan_veth_cleanup(monkeypatch):
    from agent.network import local as local_mod

    called = {"count": 0}

    class _FakeCleanupMgr:
        async def cleanup_orphaned_veths(self):
            called["count"] += 1

    monkeypatch.setattr("agent.network.cleanup.NetworkCleanupManager", _FakeCleanupMgr)

    mgr = local_mod.LocalNetworkManager()
    mgr._links = {}
    mgr._networks = {}

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

    plugin = MagicMock()
    plugin.delete_management_network = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "agent.providers.docker.get_docker_ovs_plugin",
        lambda: plugin,
    )

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
