from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock


from agent.network.docker_plugin import DockerOVSPlugin
from agent.providers.docker import DockerProvider


def _run(coro):
    return asyncio.run(coro)


def test_prune_legacy_lab_networks_removes_unused_legacy(monkeypatch):
    provider = DockerProvider()
    lab_id = "e844e435-fde4-4d95-98c3-4fa8966362f9"

    # Legacy network with truncated prefix (old [:20] format) — should be removed
    legacy_net = MagicMock()
    legacy_net.name = "e844e435-fde4-4d95-9-eth1"
    legacy_net.attrs = {"Labels": {}, "Containers": {}}

    # Current network with full lab_id prefix — should be kept
    current_net = MagicMock()
    current_net.name = f"{lab_id}-eth1"
    current_net.attrs = {"Labels": {}, "Containers": {}}

    docker_client = MagicMock()
    docker_client.networks.list.return_value = [legacy_net, current_net]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(provider._prune_legacy_lab_networks(lab_id))

    legacy_net.remove.assert_called_once()
    current_net.remove.assert_not_called()


def test_prune_legacy_networks_disconnects_containers(monkeypatch):
    """Legacy networks with attached containers should be disconnected then removed."""
    provider = DockerProvider()
    lab_id = "e844e435-fde4-4d95-98c3-4fa8966362f9"

    legacy_net = MagicMock()
    legacy_net.name = "e844e435-fde4-4d95-9-eth2"
    legacy_net.attrs = {"Labels": {}, "Containers": {"abc123": {}}}

    docker_client = MagicMock()
    docker_client.networks.list.return_value = [legacy_net]
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    _run(provider._prune_legacy_lab_networks(lab_id))

    legacy_net.disconnect.assert_called_once_with("abc123", force=True)
    legacy_net.remove.assert_called_once()


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
