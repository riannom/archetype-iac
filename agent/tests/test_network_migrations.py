from __future__ import annotations

import asyncio
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


def test_plugin_deserialize_ignores_old_management_fields():
    """_deserialize_state loads correctly when state has removed management_networks/next_mgmt_subnet_index."""
    plugin = DockerOVSPlugin()

    state = {
        "version": 1,
        "global_next_vlan": 150,
        "global_next_linked_vlan": 2100,
        "lab_bridges": {
            "lab1": {
                "lab_id": "lab1",
                "bridge_name": "arch-ovs",
                "next_vlan": 110,
                "network_ids": ["net-aaa"],
                "last_activity": "2026-01-01T00:00:00+00:00",
                "vxlan_tunnels": {},
                "external_ports": {},
            }
        },
        "networks": {
            "net-aaa": {
                "network_id": "net-aaa",
                "lab_id": "lab1",
                "interface_name": "eth0",
                "bridge_name": "arch-ovs",
            }
        },
        "endpoints": {
            "ep-111": {
                "endpoint_id": "ep-111",
                "network_id": "net-aaa",
                "interface_name": "eth0",
                "host_veth": "veth-abc",
                "cont_veth": "veth-def",
                "vlan_tag": 150,
                "container_name": "archetype-lab1-n1",
            }
        },
        # Old keys that were removed — must not cause errors
        "next_mgmt_subnet_index": 5,
        "management_networks": {
            "lab1": {
                "lab_id": "lab1",
                "network_id": "mgmt-net-123",
                "network_name": "archetype-mgmt-lab1",
                "subnet": "172.20.1.0/24",
                "gateway": "172.20.1.1",
            }
        },
    }

    # Should not raise
    plugin._deserialize_state(state)

    # Verify real data loaded correctly
    assert "lab1" in plugin.lab_bridges
    assert plugin.lab_bridges["lab1"].bridge_name == "arch-ovs"
    assert "net-aaa" in plugin.networks
    assert plugin.networks["net-aaa"].interface_name == "eth0"
    assert "ep-111" in plugin.endpoints
    assert plugin.endpoints["ep-111"].vlan_tag == 150
    assert plugin._global_next_vlan == 150

    # Old management fields must not be loaded as attributes
    assert not hasattr(plugin, "management_networks")
    assert not hasattr(plugin, "next_mgmt_subnet_index")


def test_plugin_serialize_excludes_management_fields():
    """_serialize_state output must not contain management network keys."""
    plugin = DockerOVSPlugin()

    # Populate minimal state
    from agent.network.docker_plugin import LabBridge, NetworkState
    from datetime import datetime, timezone

    plugin.lab_bridges["lab1"] = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
        next_vlan=110,
        network_ids={"net-aaa"},
        last_activity=datetime.now(timezone.utc),
    )
    plugin.networks["net-aaa"] = NetworkState(
        network_id="net-aaa",
        lab_id="lab1",
        interface_name="eth0",
        bridge_name="arch-ovs",
    )

    serialized = plugin._serialize_state()

    # Must not contain removed management keys
    assert "management_networks" not in serialized
    assert "next_mgmt_subnet_index" not in serialized

    # Must contain actual state
    assert "lab_bridges" in serialized
    assert "lab1" in serialized["lab_bridges"]
    assert "networks" in serialized
    assert "net-aaa" in serialized["networks"]
    assert serialized["global_next_vlan"] == plugin._global_next_vlan


