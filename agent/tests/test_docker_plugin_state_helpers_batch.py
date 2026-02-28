from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.network import docker_plugin as plugin_mod
from agent.network.docker_plugin import (
    LINKED_VLAN_END,
    LINKED_VLAN_START,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    NetworkState,
)


def test_generate_veth_names_prefix_and_length(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    monkeypatch.setattr(plugin_mod.secrets, "token_hex", lambda _n: "abc123")
    plugin = DockerOVSPlugin()

    host_veth, cont_veth = plugin._generate_veth_names("endpoint-123456789")
    assert host_veth.startswith("vhendpo")
    assert cont_veth.startswith("vcendpo")
    assert len(host_veth) <= 15
    assert len(cont_veth) <= 15


def test_release_vlan_and_release_linked_vlan_clear_tracking_sets(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    plugin._allocated_vlans.update({101, 2051})
    plugin._allocated_linked_vlans.update({2051, 3000})

    plugin._release_vlan(2051)
    assert 2051 not in plugin._allocated_vlans
    assert 2051 not in plugin._allocated_linked_vlans

    plugin._release_linked_vlan(3000)
    assert 3000 not in plugin._allocated_linked_vlans


def test_touch_lab_updates_last_activity(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    plugin.lab_bridges["lab1"] = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
        last_activity=old,
    )

    plugin._touch_lab("lab1")
    assert plugin.lab_bridges["lab1"].last_activity > old

    # Missing lab is a no-op.
    plugin._touch_lab("missing")


def test_deserialize_state_handles_invalid_timestamp_and_vlan_wrap(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    state = {
        "version": 99,  # unknown versions should still attempt load
        "lab_bridges": {
            "lab1": {
                "lab_id": "lab1",
                "bridge_name": "arch-ovs",
                "next_vlan": 150,
                "network_ids": ["net1"],
                "last_activity": "not-iso",
                "vxlan_tunnels": {"5000": "vxlan5000"},
                "external_ports": {"eth9": 2999},
            }
        },
        "networks": {
            "net1": {
                "network_id": "net1",
                "lab_id": "lab1",
                "interface_name": "eth1",
                "bridge_name": "arch-ovs",
            }
        },
        "endpoints": {
            "ep1": {
                "endpoint_id": "ep1",
                "network_id": "net1",
                "interface_name": "eth1",
                "host_veth": "vh1",
                "cont_veth": "vc1",
                "vlan_tag": VLAN_RANGE_END,
                "container_name": "c1",
            }
        },
        # omit global_next_vlan to exercise computed next-vlan wrap branch
    }

    plugin._deserialize_state(state)

    assert "lab1" in plugin.lab_bridges
    assert isinstance(plugin.lab_bridges["lab1"].last_activity, datetime)
    assert plugin._allocated_vlans == {VLAN_RANGE_END}
    assert plugin._global_next_vlan == VLAN_RANGE_START
    assert plugin._global_next_linked_vlan == LINKED_VLAN_START


@pytest.mark.asyncio
async def test_save_and_load_state_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin._global_next_vlan = 155
    plugin._global_next_linked_vlan = 2055
    plugin.lab_bridges["lab1"] = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
        network_ids={"net1"},
    )
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=155,
        container_name="c1",
    )

    await plugin._save_state()
    assert plugin._state_file.exists()

    reloaded = DockerOVSPlugin()
    loaded = await reloaded._load_state()
    assert loaded is True
    assert "lab1" in reloaded.lab_bridges
    assert "net1" in reloaded.networks
    assert "ep1" in reloaded.endpoints
    assert reloaded._global_next_linked_vlan == 2055


@pytest.mark.asyncio
async def test_load_state_returns_false_for_corrupt_json(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin._state_file.write_text("{ not-json")

    assert await plugin._load_state() is False


@pytest.mark.asyncio
async def test_mark_dirty_and_save_marks_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin._save_state = AsyncMock()

    await plugin._mark_dirty_and_save()
    assert plugin._state_dirty is True
    plugin._save_state.assert_awaited_once()


def test_queue_missing_endpoint_reconnect_guards_and_enqueues(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )

    no_container = EndpointState(
        endpoint_id="ep0",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh0",
        cont_veth="vc0",
        vlan_tag=100,
        container_name=None,
    )
    assert plugin._queue_missing_endpoint_reconnect(no_container) is False

    missing_network = EndpointState(
        endpoint_id="epx",
        network_id="missing",
        interface_name="eth1",
        host_veth="vhx",
        cont_veth="vcx",
        vlan_tag=101,
        container_name="c1",
    )
    assert plugin._queue_missing_endpoint_reconnect(missing_network) is False

    valid = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=102,
        container_name="c1",
    )
    assert plugin._queue_missing_endpoint_reconnect(valid) is True
    assert plugin._pending_endpoint_reconnects == [("c1", "net1", "eth1")]


@pytest.mark.asyncio
async def test_reconnect_pending_endpoints_consumes_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin._pending_endpoint_reconnects = [
        ("c1", "net1", "eth1"),
        ("c2", "missing-net", "eth2"),
    ]

    plugin._reconnect_container_to_network = AsyncMock(side_effect=[True, False])
    await plugin._reconnect_pending_endpoints()

    assert plugin._pending_endpoint_reconnects == []
    assert plugin._reconnect_container_to_network.await_count == 2


def test_state_view_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
        network_ids={"net1"},
        external_ports={"ens1": 2001},
    )
    plugin.networks["net1"] = NetworkState(
        network_id="net1-abcdef123456",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1-abcdef123456",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=300,
        container_name="c1",
    )
    plugin.endpoints["ep2"] = EndpointState(
        endpoint_id="ep2-abcdef123456",
        network_id="net1",
        interface_name="eth2",
        host_veth="vh2",
        cont_veth="vc2",
        vlan_tag=450,
        container_name="c2",
    )

    assert plugin.list_external_interfaces("lab1") == {"ens1": 2001}
    assert plugin.list_external_interfaces("missing") == {}
    assert plugin.get_lab_vlan_range("lab1") == (300, 450)
    assert plugin.get_lab_vlan_range("missing") == (0, 0)
    assert plugin.get_all_labs() == ["lab1"]
    assert plugin.get_active_host_veths() == {"vh1", "vh2"}

    status = plugin.get_lab_status("lab1")
    assert status is not None
    assert status["lab_id"] == "lab1"
    assert status["bridge_name"] == "arch-ovs"
    assert {n["interface_name"] for n in status["networks"]} == {"eth1"}
    assert {ep["host_veth"] for ep in status["endpoints"]} == {"vh1", "vh2"}

    assert plugin.get_lab_status("missing") is None


@pytest.mark.asyncio
async def test_allocate_vlan_fallback_paths_and_exhaustion(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    lab = LabBridge(lab_id="lab1", bridge_name="arch-ovs")

    # Exhaust isolated range to force linked-range fallback.
    plugin._allocated_vlans = set(range(VLAN_RANGE_START, VLAN_RANGE_END + 1))
    plugin._get_used_vlan_tags_on_bridge = AsyncMock(return_value=set())
    plugin._global_next_linked_vlan = LINKED_VLAN_START
    vlan = await plugin._allocate_vlan(lab)
    assert vlan == LINKED_VLAN_START
    assert LINKED_VLAN_START in plugin._allocated_linked_vlans

    # Exhaust linked range to force isolated-range fallback.
    plugin._allocated_linked_vlans = set(range(LINKED_VLAN_START, LINKED_VLAN_END + 1))
    plugin._allocated_vlans = set()
    plugin._global_next_vlan = VLAN_RANGE_START
    linked_vlan = await plugin._allocate_linked_vlan(lab)
    assert linked_vlan == VLAN_RANGE_START
    assert VLAN_RANGE_START in plugin._allocated_vlans

    # Exhaust both ranges to trigger hard failure.
    plugin._allocated_vlans = set(range(VLAN_RANGE_START, VLAN_RANGE_END + 1))
    plugin._allocated_linked_vlans = set(range(LINKED_VLAN_START, LINKED_VLAN_END + 1))
    with pytest.raises(RuntimeError, match="No available VLAN tags"):
        await plugin._allocate_vlan(lab)


@pytest.mark.asyncio
async def test_status_ports_and_flows_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(
        lab_id="lab1",
        bridge_name="arch-ovs",
    )
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=222,
        container_name="c1",
    )

    plugin._check_ovs_health = AsyncMock(return_value=True)

    async def _run_cmd(cmd):
        if cmd[:4] == ["ovs-vsctl", "get", "interface", "vh1"]:
            return 0, "{rx_bytes=10, tx_bytes=20}", ""
        if cmd[:2] == ["ovs-ofctl", "dump-flows"]:
            return 0, "NXST_FLOW reply\n cookie=0x0, actions=normal", ""
        return 1, "", "error"

    plugin._run_cmd = _run_cmd

    status = await plugin.get_plugin_status()
    assert status["healthy"] is True
    assert status["labs_count"] == 1
    assert status["endpoints_count"] == 1
    assert status["bridges"][0]["vlan_range_used"] == (222, 222)

    ports = await plugin.get_lab_ports("lab1")
    assert ports and ports[0]["rx_bytes"] == 10 and ports[0]["tx_bytes"] == 20
    assert await plugin.get_lab_ports("missing") == []

    flows = await plugin.get_lab_flows("lab1")
    assert flows["flow_count"] == 1
    assert "actions=normal" in flows["flows"][0]
    assert await plugin.get_lab_flows("missing") == {"error": "Lab not found"}


@pytest.mark.asyncio
async def test_create_and_delete_network_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin._mark_dirty_and_save = AsyncMock()

    lab_bridge = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.lab_bridges["lab1"] = lab_bridge
    plugin._ensure_bridge = AsyncMock(return_value=lab_bridge)
    plugin._maybe_delete_bridge = AsyncMock()

    create_req = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "NetworkID": "net1",
                "Options": {
                    "com.docker.network.generic": {
                        "lab_id": "lab1",
                        "interface_name": "eth3",
                    }
                },
            }
        )
    )
    create_resp = await plugin.handle_create_network(create_req)
    assert create_resp.status == 200
    assert json.loads(create_resp.text) == {}
    assert "net1" in plugin.networks
    assert "net1" in lab_bridge.network_ids

    delete_req = SimpleNamespace(json=AsyncMock(return_value={"NetworkID": "net1"}))
    delete_resp = await plugin.handle_delete_network(delete_req)
    assert delete_resp.status == 200
    assert json.loads(delete_resp.text) == {}
    assert "net1" not in plugin.networks
    plugin._maybe_delete_bridge.assert_awaited_once_with("lab1")
    assert plugin._mark_dirty_and_save.await_count == 2


@pytest.mark.asyncio
async def test_handle_create_network_requires_lab_id(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    req = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "NetworkID": "net1",
                "Options": {"com.docker.network.generic": {"interface_name": "eth1"}},
            }
        )
    )
    resp = await plugin.handle_create_network(req)
    assert json.loads(resp.text)["Err"] == "lab_id option is required"


@pytest.mark.asyncio
async def test_endpoint_handler_happy_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin._allocate_vlan = AsyncMock(return_value=222)
    plugin._create_veth_pair = AsyncMock(return_value=True)
    plugin._attach_to_ovs = AsyncMock(return_value=True)
    plugin._generate_veth_names = lambda _endpoint_id: ("vh-fixed", "vc-fixed")
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._release_vlan = MagicMock()
    plugin._delete_port = AsyncMock()

    create_req = SimpleNamespace(json=AsyncMock(return_value={"NetworkID": "net1", "EndpointID": "ep1"}))
    create_resp = await plugin.handle_create_endpoint(create_req)
    assert create_resp.status == 200
    assert json.loads(create_resp.text) == {"Interface": {}}
    assert "ep1" in plugin.endpoints

    join_req = SimpleNamespace(json=AsyncMock(return_value={"EndpointID": "ep1", "SandboxKey": "sbx"}))
    join_resp = await plugin.handle_join(join_req)
    join_body = json.loads(join_resp.text)
    assert join_body["InterfaceName"]["DstName"] == "eth1"

    leave_resp = await plugin.handle_leave(SimpleNamespace(json=AsyncMock(return_value={"EndpointID": "ep1"})))
    assert leave_resp.status == 200 and json.loads(leave_resp.text) == {}

    info_resp = await plugin.handle_endpoint_oper_info(
        SimpleNamespace(json=AsyncMock(return_value={"EndpointID": "ep1"}))
    )
    assert json.loads(info_resp.text) == {"Value": {}}

    del_resp = await plugin.handle_delete_endpoint(
        SimpleNamespace(json=AsyncMock(return_value={"NetworkID": "net1", "EndpointID": "ep1"}))
    )
    assert del_resp.status == 200
    assert "ep1" not in plugin.endpoints
    plugin._release_vlan.assert_called_once_with(222)
    plugin._delete_port.assert_awaited_once_with("arch-ovs", "vh-fixed")


@pytest.mark.asyncio
async def test_endpoint_handlers_error_paths_and_static_handlers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    missing_network_resp = await plugin.handle_create_endpoint(
        SimpleNamespace(json=AsyncMock(return_value={"NetworkID": "missing", "EndpointID": "ep1"}))
    )
    assert "not found" in json.loads(missing_network_resp.text)["Err"]

    join_resp = await plugin.handle_join(
        SimpleNamespace(json=AsyncMock(return_value={"EndpointID": "missing"}))
    )
    assert "not found" in json.loads(join_resp.text)["Err"]

    assert json.loads((await plugin.handle_activate(SimpleNamespace())).text) == {"Implements": ["NetworkDriver"]}
    caps = json.loads((await plugin.handle_get_capabilities(SimpleNamespace())).text)
    assert caps["Scope"] == "local"
    assert json.loads((await plugin.handle_discover_new(SimpleNamespace())).text) == {}
    assert json.loads((await plugin.handle_discover_delete(SimpleNamespace())).text) == {}
    assert json.loads((await plugin.handle_program_external_connectivity(SimpleNamespace())).text) == {}
    assert json.loads((await plugin.handle_revoke_external_connectivity(SimpleNamespace())).text) == {}


@pytest.mark.asyncio
async def test_endpoint_vlan_sync_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=150,
        container_name="c1",
    )
    plugin._allocated_vlans.add(150)
    plugin._mark_dirty_and_save = AsyncMock()

    assert await plugin.set_endpoint_vlan_by_host_veth("lab1", "vh1", LINKED_VLAN_START) is True
    assert plugin.endpoints["ep1"].vlan_tag == LINKED_VLAN_START
    assert 150 not in plugin._allocated_vlans
    assert LINKED_VLAN_START in plugin._allocated_linked_vlans

    save_calls = plugin._mark_dirty_and_save.await_count
    assert await plugin.set_endpoint_vlan_by_host_veth("lab1", "vh1", LINKED_VLAN_START) is True
    assert plugin._mark_dirty_and_save.await_count == save_calls

    assert await plugin.set_endpoint_vlan_by_host_veth("lab1", "missing", 123) is False


@pytest.mark.asyncio
async def test_container_mapping_and_endpoint_name_helpers(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.networks["net1"] = NetworkState(
        network_id="net1",
        lab_id="lab1",
        interface_name="eth1",
        bridge_name="arch-ovs",
    )
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=100,
        container_name=None,
    )
    plugin._mark_dirty_and_save = AsyncMock()

    await plugin.set_endpoint_container_name("ep1", "c1")
    assert plugin.endpoints["ep1"].container_name == "c1"
    plugin._mark_dirty_and_save.assert_awaited_once()

    mapping = plugin.get_container_interface_mapping("lab1", "c1")
    assert mapping == {"vh1": "eth1"}
    assert await plugin.get_endpoint_host_veth("lab1", "c1", "eth1") == "vh1"

    # Fallback mapping from network definitions when endpoint state is absent.
    plugin.endpoints.clear()
    mapping_fallback = plugin.get_container_interface_mapping("lab1", "c1")
    assert mapping_fallback == {"network:net1": "eth1"}

    discovered = EndpointState(
        endpoint_id="epX",
        network_id="net1",
        interface_name="eth9",
        host_veth="vhX",
        cont_veth="vcX",
        vlan_tag=123,
        container_name="c1",
    )
    plugin._discover_endpoint = AsyncMock(return_value=discovered)
    assert await plugin.get_endpoint_host_veth("lab1", "c1", "eth9") == "vhX"


@pytest.mark.asyncio
async def test_hot_disconnect_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin._mark_dirty_and_save = AsyncMock()

    # Missing lab
    assert await plugin.hot_disconnect("lab1", "c1", "eth1") is None

    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")

    # Missing endpoint
    assert await plugin.hot_disconnect("lab1", "c1", "eth1") is None

    ep = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=100,
        container_name="c1",
    )
    plugin.endpoints["ep1"] = ep
    plugin._allocate_vlan = AsyncMock(return_value=333)
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

    assert await plugin.hot_disconnect("lab1", "c1", "eth1") == 333
    assert plugin.endpoints["ep1"].vlan_tag == 333
    plugin._mark_dirty_and_save.assert_awaited_once()

    plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "set-failed"))
    assert await plugin.hot_disconnect("lab1", "c1", "eth1") is None


@pytest.mark.asyncio
async def test_set_carrier_state_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    assert await plugin.set_carrier_state("lab1", "c1", "eth1", "invalid") is False

    plugin._get_container_pid = AsyncMock(return_value=None)
    assert await plugin.set_carrier_state("lab1", "c1", "eth1", "off") is False

    plugin._get_container_pid = AsyncMock(return_value=1234)
    plugin._run_cmd = AsyncMock(return_value=(1, "", "fail"))
    assert await plugin.set_carrier_state("lab1", "c1", "eth1", "off") is False

    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    assert await plugin.set_carrier_state("lab1", "c1", "eth1", "on") is True


@pytest.mark.asyncio
async def test_isolate_and_restore_port_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    plugin.hot_disconnect = AsyncMock(return_value=None)
    assert await plugin.isolate_port("lab1", "c1", "eth1") is None

    plugin.hot_disconnect = AsyncMock(return_value=444)
    plugin.set_carrier_state = AsyncMock(return_value=False)
    assert await plugin.isolate_port("lab1", "c1", "eth1") == 444

    # Restore: missing lab
    assert await plugin.restore_port("lab1", "c1", "eth1", 200) is False

    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin._mark_dirty_and_save = AsyncMock()

    # Restore: endpoint missing
    assert await plugin.restore_port("lab1", "c1", "eth1", 200) is False

    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=100,
        container_name="c1",
    )

    plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "set-failed"))
    assert await plugin.restore_port("lab1", "c1", "eth1", 200) is False

    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin.set_carrier_state = AsyncMock(return_value=False)
    assert await plugin.restore_port("lab1", "c1", "eth1", 200) is False

    plugin.set_carrier_state = AsyncMock(return_value=True)
    assert await plugin.restore_port("lab1", "c1", "eth1", 201) is True
    assert plugin.endpoints["ep1"].vlan_tag == 201


@pytest.mark.asyncio
async def test_get_and_set_endpoint_vlan_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()
    plugin.lab_bridges["lab1"] = LabBridge(lab_id="lab1", bridge_name="arch-ovs")
    plugin.endpoints["ep1"] = EndpointState(
        endpoint_id="ep1",
        network_id="net1",
        interface_name="eth1",
        host_veth="vh1",
        cont_veth="vc1",
        vlan_tag=150,
        container_name="c1",
    )
    plugin._mark_dirty_and_save = AsyncMock()

    assert await plugin.get_endpoint_vlan("lab1", "c1", "eth1") == 150

    plugin._run_cmd = AsyncMock(return_value=(0, "321", ""))
    assert await plugin.get_endpoint_vlan("lab1", "c1", "eth1", read_from_ovs=True) == 321

    plugin._run_cmd = AsyncMock(return_value=(0, "not-an-int", ""))
    assert await plugin.get_endpoint_vlan("lab1", "c1", "eth1", read_from_ovs=True) is None

    assert await plugin.set_endpoint_vlan("lab1", "c1", "eth1", 222) is True
    assert plugin.endpoints["ep1"].vlan_tag == 222

    discovered = EndpointState(
        endpoint_id="ep2",
        network_id="net2",
        interface_name="eth9",
        host_veth="vh9",
        cont_veth="vc9",
        vlan_tag=111,
        container_name="c1",
    )
    plugin._discover_endpoint = AsyncMock(return_value=discovered)
    assert await plugin.set_endpoint_vlan("lab1", "c1", "eth9", 333) is True
    assert plugin.endpoints["ep2"].vlan_tag == 333

    plugin._discover_endpoint = AsyncMock(return_value=None)
    assert await plugin.set_endpoint_vlan("lab1", "c1", "ethX", 444) is False
