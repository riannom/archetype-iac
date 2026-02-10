from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from docker.errors import NotFound

from agent.network.docker_plugin import DockerOVSPlugin
from agent.providers.docker import DockerProvider


def _run(coro):
    return asyncio.run(coro)


def test_prune_legacy_lab_networks_removes_unused_legacy(monkeypatch):
    provider = DockerProvider()

    legacy_net = MagicMock()
    legacy_net.name = "legacy$lab-eth1"
    legacy_net.attrs = {"Labels": {}, "Containers": {}}

    safe_net = MagicMock()
    safe_net.name = "legacylab-eth1"
    safe_net.attrs = {"Labels": {}, "Containers": {}}

    docker_client = MagicMock()
    docker_client.networks.list.return_value = [legacy_net, safe_net]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(provider._prune_legacy_lab_networks("legacy$lab"))

    legacy_net.remove.assert_called_once()
    safe_net.remove.assert_not_called()


def test_plugin_mgmt_network_recreated_for_owner_label(monkeypatch):
    plugin = DockerOVSPlugin()

    existing = MagicMock()
    existing.id = "net123"
    existing.attrs = {
        "Labels": {"archetype.lab_id": "lab1", "archetype.type": "management"},
        "Containers": {},
        "IPAM": {"Config": [{"Subnet": "172.20.1.0/24", "Gateway": "172.20.1.1"}]},
    }

    created = SimpleNamespace(id="net456")

    client = MagicMock()
    client.networks.get.side_effect = [existing]
    client.networks.create.return_value = created

    def _fake_from_env():
        return client

    monkeypatch.setattr("docker.from_env", _fake_from_env)

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(plugin.create_management_network("lab1"))

    existing.remove.assert_called_once()
    client.networks.create.assert_called_once()
