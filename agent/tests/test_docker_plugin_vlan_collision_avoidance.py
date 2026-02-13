import pytest


@pytest.mark.asyncio
async def test_docker_plugin_allocate_vlan_skips_tags_in_use_on_ovs_bridge(monkeypatch):
    # Import inside test so pytest collection doesn't require Docker/OVS.
    from agent.network.docker_plugin import DockerOVSPlugin

    plugin = DockerOVSPlugin()
    plugin._global_next_vlan = 2002
    plugin._allocated_vlans = set()

    async def fake_ovs_vsctl(*args: str):
        # Bridge membership
        if args == ("list-ports", "arch-ovs"):
            return 0, "vnet605\nvnet606\nvh123\n", ""

        # Global port tag dump
        if args == ("--format=csv", "--columns=name,tag", "list", "port"):
            return 0, "name,tag\nvnet605,2002\nvnet606,2003\nvh123,101\n", ""

        return 1, "", "unexpected command"

    # Force bridge name for deterministic tests.
    import agent.network.docker_plugin as dp

    monkeypatch.setattr(dp.settings, "ovs_bridge_name", "arch-ovs", raising=False)
    monkeypatch.setattr(plugin, "_ovs_vsctl", fake_ovs_vsctl)

    # lab_bridge isn't used by the allocator, but keep signature realistic.
    lab_bridge = type("LB", (), {"lab_id": "lab1"})()

    vlan = await plugin._allocate_vlan(lab_bridge)
    assert vlan == 2004
    assert plugin._global_next_vlan == 2005

