from __future__ import annotations

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.ovs import OVSNetworkManager


@pytest.fixture(autouse=True)
def _reset_ovs_singleton():
    OVSNetworkManager._instance = None
    yield
    OVSNetworkManager._instance = None


@pytest.mark.asyncio
async def test_discover_existing_state_scans_containers_once():
    mgr = OVSNetworkManager()
    mgr._init_state()
    mgr._bridge_name = "arch-ovs"

    c1 = MagicMock()
    c1.name = "archetype-lab1-r1"
    c1.attrs = {"State": {"Pid": 111}}
    c1.labels = {"archetype.lab_id": "lab1"}

    c2 = MagicMock()
    c2.name = "archetype-lab1-r2"
    c2.attrs = {"State": {"Pid": 222}}
    c2.labels = {"archetype.lab_id": "lab1"}

    docker_client = MagicMock()
    docker_client.containers.list.return_value = [c1, c2]
    mgr._docker = docker_client

    # Batch JSON response for port tags (new batched query format)
    batch_port_json = _json.dumps({
        "data": [
            ["vhport1", 2001],
            ["vhport2", 2002],
        ]
    })

    async def ovs_vsctl_side_effect(*args: str):
        if args == ("list-ports", "arch-ovs"):
            return 0, "vhport1\nvhport2", ""
        # Batched port tag query
        if args == ("--format=json", "--", "--columns=name,tag", "list", "Port"):
            return 0, batch_port_json, ""
        return 1, "", "unexpected call"

    async def run_cmd_side_effect(cmd: list[str]):
        if cmd[:8] == ["nsenter", "-t", "111", "-n", "ip", "-o", "link", "show"]:
            return 0, "1: lo: <LOOPBACK>\n2: eth1@if101: <BROADCAST>", ""
        if cmd[:8] == ["nsenter", "-t", "222", "-n", "ip", "-o", "link", "show"]:
            return 0, "1: lo: <LOOPBACK>\n2: eth2@if202: <BROADCAST>", ""
        return 1, "", "unexpected command"

    mgr._ovs_vsctl = AsyncMock(side_effect=ovs_vsctl_side_effect)
    mgr._run_cmd = AsyncMock(side_effect=run_cmd_side_effect)

    # Mock asyncio.to_thread: sysfs reads return fake ifindexes,
    # Docker container list calls execute synchronously.
    _ifindex_map = {"vhport1": "101", "vhport2": "202"}

    async def _fake_to_thread(fn, *args, **kwargs):
        # Detect _read_ifindexes by checking if first arg is a list of port names
        if args and isinstance(args[0], list) and all(isinstance(x, str) for x in args[0]):
            # This is the _read_ifindexes call — return fake sysfs data
            return {name: _ifindex_map[name] for name in args[0] if name in _ifindex_map}
        # Everything else (Docker calls) runs synchronously
        return fn(*args, **kwargs)

    with patch("agent.network.ovs.asyncio.to_thread", side_effect=_fake_to_thread):
        await mgr._discover_existing_state()

    assert "archetype-lab1-r1:eth1" in mgr._ports
    assert "archetype-lab1-r2:eth2" in mgr._ports
    assert docker_client.containers.list.call_count == 1

    nsenter_calls = [
        call.args[0]
        for call in mgr._run_cmd.call_args_list
        if call.args and call.args[0][:2] == ["nsenter", "-t"]
    ]
    assert len(nsenter_calls) == 2
