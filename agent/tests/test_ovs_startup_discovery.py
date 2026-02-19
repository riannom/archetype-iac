from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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

    async def ovs_vsctl_side_effect(*args: str):
        if args == ("list-ports", "arch-ovs"):
            return 0, "vhport1\nvhport2", ""
        if args == ("get", "port", "vhport1", "tag"):
            return 0, "2001", ""
        if args == ("get", "port", "vhport2", "tag"):
            return 0, "2002", ""
        return 1, "", "unexpected call"

    async def run_cmd_side_effect(cmd: list[str]):
        if cmd[:7] == ["nsenter", "-t", "111", "-n", "ip", "-o", "link"]:
            return 0, "1: lo: <LOOPBACK>\n2: eth1@if101: <BROADCAST>", ""
        if cmd[:7] == ["nsenter", "-t", "222", "-n", "ip", "-o", "link"]:
            return 0, "1: lo: <LOOPBACK>\n2: eth2@if202: <BROADCAST>", ""
        if cmd == ["cat", "/sys/class/net/vhport1/ifindex"]:
            return 0, "101\n", ""
        if cmd == ["cat", "/sys/class/net/vhport2/ifindex"]:
            return 0, "202\n", ""
        return 1, "", "unexpected command"

    mgr._ovs_vsctl = AsyncMock(side_effect=ovs_vsctl_side_effect)
    mgr._run_cmd = AsyncMock(side_effect=run_cmd_side_effect)

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
