"""Edge-case tests for the Docker OVS plugin.

Covers ifindex resolution, carrier propagation, VLAN management,
multi-interface behaviour, and port-swap prevention.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    LINKED_VLAN_END,
    LINKED_VLAN_START,
    NetworkState,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(tmp_path, monkeypatch) -> DockerOVSPlugin:
    monkeypatch.setattr(
        "agent.network.docker_plugin.settings.workspace_path", str(tmp_path)
    )
    return DockerOVSPlugin()


def _seed_lab(plugin: DockerOVSPlugin, lab_id: str = "lab1") -> LabBridge:
    bridge = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    plugin.lab_bridges[lab_id] = bridge
    return bridge


def _seed_endpoint(
    plugin: DockerOVSPlugin,
    *,
    lab_id: str = "lab1",
    endpoint_id: str = "ep1",
    network_id: str = "net1",
    interface: str = "eth1",
    host_veth: str = "vhabc1",
    cont_veth: str = "vcabc1",
    vlan: int = 100,
    container: str | None = "ctr-a",
) -> EndpointState:
    ep = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name=interface,
        host_veth=host_veth,
        cont_veth=cont_veth,
        vlan_tag=vlan,
        container_name=container,
    )
    plugin.endpoints[endpoint_id] = ep
    net = NetworkState(
        network_id=network_id,
        lab_id=lab_id,
        interface_name=interface,
        bridge_name="arch-ovs",
    )
    plugin.networks[network_id] = net
    bridge = plugin.lab_bridges.get(lab_id)
    if bridge:
        bridge.network_ids.add(network_id)
    return ep


# ---------------------------------------------------------------------------
# TestIfindexResolution
# ---------------------------------------------------------------------------

class TestIfindexResolution:
    """Tests for _resolve_ovs_port_via_ifindex in helpers.py."""

    @pytest.mark.asyncio
    async def test_returns_port_and_vlan_on_ifindex_match(self):
        """Should return (port_name, vlan_tag) when ifindex matches."""
        with (
            patch(
                "agent.helpers._resolve_ifindex_sync", return_value=42
            ),
            patch(
                "asyncio.create_subprocess_exec",
            ) as mock_exec,
            patch(
                "agent.helpers._ovs_get_port_vlan",
                new_callable=AsyncMock,
                return_value=200,
            ),
        ):
            # First call: list-ports
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(return_value=(b"vhabc\nvhdef\n", b""))
            list_proc.returncode = 0

            # Second call: get ifindex for vhabc -> no match
            idx_proc_1 = AsyncMock()
            idx_proc_1.communicate = AsyncMock(return_value=(b"99\n", b""))
            idx_proc_1.returncode = 0

            # Third call: get ifindex for vhdef -> match
            idx_proc_2 = AsyncMock()
            idx_proc_2.communicate = AsyncMock(return_value=(b"42\n", b""))
            idx_proc_2.returncode = 0

            mock_exec.side_effect = [list_proc, idx_proc_1, idx_proc_2]

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr-a", "eth1")
            assert result == ("vhdef", 200)

    @pytest.mark.asyncio
    async def test_returns_none_when_peer_ifindex_is_none(self):
        """Should return None when container exec fails to read ifindex."""
        with patch("agent.helpers._resolve_ifindex_sync", return_value=None):
            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("missing-ctr", "eth1")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_list_ports_fails(self):
        """Should return None when ovs-vsctl list-ports fails."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=42),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(return_value=(b"", b"error"))
            list_proc.returncode = 1
            mock_exec.return_value = list_proc

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr-a", "eth1")
            assert result is None

    @pytest.mark.asyncio
    async def test_skips_non_vh_ports(self):
        """Should skip ports that don't start with 'vh' prefix."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=42),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(
                return_value=(b"vtep-100\npatch-int\n", b"")
            )
            list_proc.returncode = 0
            mock_exec.return_value = list_proc

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr-a", "eth1")
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_non_numeric_ifindex_gracefully(self):
        """Should skip ports where ifindex read returns non-numeric data."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=42),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch(
                "agent.helpers._ovs_get_port_vlan",
                new_callable=AsyncMock,
                return_value=300,
            ),
        ):
            # list-ports
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(return_value=(b"vhbad\nvhgood\n", b""))
            list_proc.returncode = 0

            # First port returns garbage ifindex
            bad_idx = AsyncMock()
            bad_idx.communicate = AsyncMock(return_value=(b"not_a_number\n", b""))
            bad_idx.returncode = 0

            # Second port matches
            good_idx = AsyncMock()
            good_idx.communicate = AsyncMock(return_value=(b"42\n", b""))
            good_idx.returncode = 0

            mock_exec.side_effect = [list_proc, bad_idx, good_idx]

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr-a", "eth1")
            assert result == ("vhgood", 300)

    @pytest.mark.asyncio
    async def test_returns_zero_vlan_when_tag_missing(self):
        """Should return vlan_tag 0 when _ovs_get_port_vlan returns None."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=7),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch(
                "agent.helpers._ovs_get_port_vlan",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(return_value=(b"vhonly\n", b""))
            list_proc.returncode = 0

            idx_proc = AsyncMock()
            idx_proc.communicate = AsyncMock(return_value=(b"7\n", b""))
            idx_proc.returncode = 0

            mock_exec.side_effect = [list_proc, idx_proc]

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr-a", "eth1")
            assert result == ("vhonly", 0)


# ---------------------------------------------------------------------------
# TestCarrierPropagation
# ---------------------------------------------------------------------------

class TestCarrierPropagation:
    """Tests for carrier state management through the plugin."""

    @pytest.mark.asyncio
    async def test_set_carrier_state_rejects_invalid_state(self, monkeypatch, tmp_path):
        """Should return False for any state other than 'on' or 'off'."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        result = await plugin.set_carrier_state("lab1", "ctr", "eth1", "invalid")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_carrier_state_returns_false_when_pid_missing(
        self, monkeypatch, tmp_path
    ):
        """Should return False when container PID cannot be resolved."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=None))
        result = await plugin.set_carrier_state("lab1", "ctr", "eth1", "on")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_carrier_on_invokes_nsenter(self, monkeypatch, tmp_path):
        """Should run nsenter with 'carrier on' when PID is available."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=999))
        monkeypatch.setattr(
            plugin,
            "_run_cmd",
            AsyncMock(return_value=(0, "", "")),
        )
        result = await plugin.set_carrier_state("lab1", "ctr", "eth1", "on")
        assert result is True
        call_args = plugin._run_cmd.call_args[0][0]
        assert "nsenter" in call_args
        assert "carrier" in call_args
        assert "on" in call_args

    @pytest.mark.asyncio
    async def test_set_carrier_off_invokes_nsenter(self, monkeypatch, tmp_path):
        """Should run nsenter with 'carrier off' for link-down simulation."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr(plugin, "_get_container_pid", AsyncMock(return_value=999))
        monkeypatch.setattr(plugin, "_run_cmd", AsyncMock(return_value=(0, "", "")))
        result = await plugin.set_carrier_state("lab1", "ctr", "eth1", "off")
        assert result is True
        call_args = plugin._run_cmd.call_args[0][0]
        assert "off" in call_args

    @pytest.mark.asyncio
    async def test_isolate_port_sets_carrier_off_after_vlan_change(
        self, monkeypatch, tmp_path
    ):
        """isolate_port should hot_disconnect then set carrier off."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr(
            plugin, "hot_disconnect", AsyncMock(return_value=150)
        )
        monkeypatch.setattr(
            plugin, "set_carrier_state", AsyncMock(return_value=True)
        )
        result = await plugin.isolate_port("lab1", "ctr", "eth1")
        assert result == 150
        plugin.set_carrier_state.assert_awaited_once_with("lab1", "ctr", "eth1", "off")

    @pytest.mark.asyncio
    async def test_isolate_port_returns_vlan_even_if_carrier_fails(
        self, monkeypatch, tmp_path
    ):
        """If carrier-off fails, VLAN is still returned (L2 isolation holds)."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr(plugin, "hot_disconnect", AsyncMock(return_value=151))
        monkeypatch.setattr(
            plugin, "set_carrier_state", AsyncMock(return_value=False)
        )
        result = await plugin.isolate_port("lab1", "ctr", "eth1")
        assert result == 151


# ---------------------------------------------------------------------------
# TestVlanManagement
# ---------------------------------------------------------------------------

class TestVlanManagement:
    """Tests for VLAN allocation, release, and range management."""

    @pytest.mark.asyncio
    async def test_allocate_vlan_returns_unique_tags(self, monkeypatch, tmp_path):
        """Successive allocations must return distinct VLAN tags."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        bridge = _seed_lab(plugin)
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge", AsyncMock(return_value=set())
        )

        tags = set()
        for _ in range(5):
            tag = await plugin._allocate_vlan(bridge)
            assert tag not in tags
            tags.add(tag)

    @pytest.mark.asyncio
    async def test_allocate_vlan_wraps_around_at_range_end(self, monkeypatch, tmp_path):
        """Should wrap back to VLAN_RANGE_START after hitting VLAN_RANGE_END."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        bridge = _seed_lab(plugin)
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge", AsyncMock(return_value=set())
        )
        plugin._global_next_vlan = VLAN_RANGE_END

        tag1 = await plugin._allocate_vlan(bridge)
        assert tag1 == VLAN_RANGE_END

        tag2 = await plugin._allocate_vlan(bridge)
        assert tag2 == VLAN_RANGE_START

    @pytest.mark.asyncio
    async def test_allocate_vlan_skips_used_tags_on_bridge(
        self, monkeypatch, tmp_path
    ):
        """Should skip VLAN tags already in use on the OVS bridge."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        bridge = _seed_lab(plugin)
        plugin._global_next_vlan = VLAN_RANGE_START
        monkeypatch.setattr(
            plugin,
            "_get_used_vlan_tags_on_bridge",
            AsyncMock(return_value={VLAN_RANGE_START, VLAN_RANGE_START + 1}),
        )
        tag = await plugin._allocate_vlan(bridge)
        assert tag == VLAN_RANGE_START + 2

    @pytest.mark.asyncio
    async def test_allocate_vlan_falls_back_to_linked_range_when_isolated_exhausted(
        self, monkeypatch, tmp_path
    ):
        """Should spill into linked range when isolated is full."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        bridge = _seed_lab(plugin)
        all_isolated = set(range(VLAN_RANGE_START, VLAN_RANGE_END + 1))
        plugin._allocated_vlans = all_isolated
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge", AsyncMock(return_value=set())
        )
        tag = await plugin._allocate_vlan(bridge)
        assert LINKED_VLAN_START <= tag <= LINKED_VLAN_END

    @pytest.mark.asyncio
    async def test_allocate_vlan_raises_when_both_ranges_exhausted(
        self, monkeypatch, tmp_path
    ):
        """Should raise RuntimeError when no VLAN tags are available at all."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        bridge = _seed_lab(plugin)
        all_isolated = set(range(VLAN_RANGE_START, VLAN_RANGE_END + 1))
        all_linked = set(range(LINKED_VLAN_START, LINKED_VLAN_END + 1))
        plugin._allocated_vlans = all_isolated
        plugin._allocated_linked_vlans = all_linked
        monkeypatch.setattr(
            plugin, "_get_used_vlan_tags_on_bridge", AsyncMock(return_value=set())
        )
        with pytest.raises(RuntimeError, match="No available VLAN tags"):
            await plugin._allocate_vlan(bridge)

    def test_release_vlan_removes_from_both_pools(self, monkeypatch, tmp_path):
        """_release_vlan should discard from isolated and linked tracking."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        plugin._allocated_vlans.add(150)
        plugin._allocated_linked_vlans.add(150)
        plugin._release_vlan(150)
        assert 150 not in plugin._allocated_vlans
        assert 150 not in plugin._allocated_linked_vlans

    def test_release_linked_vlan_removes_from_both_pools(self, monkeypatch, tmp_path):
        """_release_linked_vlan should discard from both tracking sets."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        plugin._allocated_vlans.add(2100)
        plugin._allocated_linked_vlans.add(2100)
        plugin._release_linked_vlan(2100)
        assert 2100 not in plugin._allocated_vlans
        assert 2100 not in plugin._allocated_linked_vlans


# ---------------------------------------------------------------------------
# TestMultiInterface
# ---------------------------------------------------------------------------

class TestMultiInterface:
    """Tests for multi-interface management and eth0 as management."""

    @pytest.mark.asyncio
    async def test_hot_connect_fails_when_lab_bridge_missing(
        self, monkeypatch, tmp_path
    ):
        """hot_connect should return None if no lab bridge is registered."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        result = await plugin.hot_connect(
            "no-such-lab", "ctr-a", "eth1", "ctr-b", "eth1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_fails_when_endpoint_a_not_found(
        self, monkeypatch, tmp_path
    ):
        """hot_connect should return None when source endpoint is missing."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        monkeypatch.setattr(
            plugin, "_discover_endpoint", AsyncMock(return_value=None)
        )
        result = await plugin.hot_connect(
            "lab1", "ctr-a", "eth1", "ctr-b", "eth1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hot_connect_sets_shared_vlan_on_both_endpoints(
        self, monkeypatch, tmp_path
    ):
        """Both endpoints should share the same linked-range VLAN after connect."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        ep_a = _seed_endpoint(
            plugin,
            endpoint_id="ep-a",
            network_id="net-a",
            interface="eth1",
            host_veth="vha",
            container="ctr-a",
            vlan=100,
        )
        ep_b = _seed_endpoint(
            plugin,
            endpoint_id="ep-b",
            network_id="net-b",
            interface="eth1",
            host_veth="vhb",
            container="ctr-b",
            vlan=101,
        )
        monkeypatch.setattr(
            plugin, "_validate_endpoint_exists", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))
        monkeypatch.setattr(
            plugin, "_allocate_linked_vlan", AsyncMock(return_value=2050)
        )
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())

        result = await plugin.hot_connect("lab1", "ctr-a", "eth1", "ctr-b", "eth1")
        assert result == 2050
        assert ep_a.vlan_tag == 2050
        assert ep_b.vlan_tag == 2050

    @pytest.mark.asyncio
    async def test_hot_connect_releases_old_vlans(self, monkeypatch, tmp_path):
        """Old isolated-range VLANs should be released after hot_connect."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        _seed_endpoint(
            plugin,
            endpoint_id="ep-a",
            network_id="net-a",
            interface="eth1",
            host_veth="vha",
            container="ctr-a",
            vlan=110,
        )
        _seed_endpoint(
            plugin,
            endpoint_id="ep-b",
            network_id="net-b",
            interface="eth1",
            host_veth="vhb",
            container="ctr-b",
            vlan=111,
        )
        plugin._allocated_vlans.update({110, 111})
        monkeypatch.setattr(
            plugin, "_validate_endpoint_exists", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))
        monkeypatch.setattr(
            plugin, "_allocate_linked_vlan", AsyncMock(return_value=2060)
        )
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())

        await plugin.hot_connect("lab1", "ctr-a", "eth1", "ctr-b", "eth1")
        assert 110 not in plugin._allocated_vlans
        assert 111 not in plugin._allocated_vlans

    @pytest.mark.asyncio
    async def test_hot_connect_rejects_cross_lab_endpoints(
        self, monkeypatch, tmp_path
    ):
        """Endpoints from different labs must not be hot-connected."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin, "lab1")
        _seed_lab(plugin, "lab2")
        _seed_endpoint(
            plugin,
            lab_id="lab1",
            endpoint_id="ep-a",
            network_id="net-a",
            interface="eth1",
            host_veth="vha",
            container="ctr-a",
        )
        _seed_endpoint(
            plugin,
            lab_id="lab2",
            endpoint_id="ep-b",
            network_id="net-b",
            interface="eth1",
            host_veth="vhb",
            container="ctr-b",
        )
        monkeypatch.setattr(
            plugin, "_validate_endpoint_exists", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())

        result = await plugin.hot_connect("lab1", "ctr-a", "eth1", "ctr-b", "eth1")
        assert result is None

    def test_get_container_interface_mapping_uses_endpoint_state(
        self, monkeypatch, tmp_path
    ):
        """Mapping should come from endpoint state when available."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        _seed_endpoint(
            plugin,
            endpoint_id="ep1",
            network_id="net1",
            interface="eth1",
            host_veth="vh001",
            container="ctr-a",
        )
        _seed_endpoint(
            plugin,
            endpoint_id="ep2",
            network_id="net2",
            interface="eth2",
            host_veth="vh002",
            container="ctr-a",
        )
        mapping = plugin.get_container_interface_mapping("lab1", "ctr-a")
        assert mapping == {"vh001": "eth1", "vh002": "eth2"}

    def test_get_container_interface_mapping_excludes_other_containers(
        self, monkeypatch, tmp_path
    ):
        """Mapping must not leak interfaces from different containers."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        _seed_endpoint(
            plugin, endpoint_id="ep1", network_id="net1",
            interface="eth1", host_veth="vh001", container="ctr-a",
        )
        _seed_endpoint(
            plugin, endpoint_id="ep2", network_id="net2",
            interface="eth1", host_veth="vh002", container="ctr-b",
        )
        mapping = plugin.get_container_interface_mapping("lab1", "ctr-a")
        assert "vh002" not in mapping

    @pytest.mark.asyncio
    async def test_get_endpoint_vlan_returns_in_memory_value(
        self, monkeypatch, tmp_path
    ):
        """Default read should return the in-memory vlan_tag."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        _seed_endpoint(
            plugin, vlan=250, container="ctr-a", interface="eth2",
            endpoint_id="ep-x", network_id="net-x", host_veth="vhx",
        )
        tag = await plugin.get_endpoint_vlan("lab1", "ctr-a", "eth2")
        assert tag == 250

    @pytest.mark.asyncio
    async def test_set_endpoint_vlan_by_host_veth_updates_tracking(
        self, monkeypatch, tmp_path
    ):
        """VLAN update by host veth should update in-memory state and pools."""
        plugin = _make_plugin(tmp_path, monkeypatch)
        _seed_lab(plugin)
        ep = _seed_endpoint(
            plugin,
            vlan=200,
            host_veth="vhtarget",
            endpoint_id="ep-t",
            network_id="net-t",
        )
        plugin._allocated_vlans.add(200)
        monkeypatch.setattr(plugin, "_mark_dirty_and_save", AsyncMock())

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhtarget", 2100)
        assert result is True
        assert ep.vlan_tag == 2100
        assert 200 not in plugin._allocated_vlans
        assert 2100 in plugin._allocated_linked_vlans


# ---------------------------------------------------------------------------
# TestPortSwapPrevention
# ---------------------------------------------------------------------------

class TestPortSwapPrevention:
    """Tests that ifindex-based resolution prevents veth-to-interface swap bugs."""

    @pytest.mark.asyncio
    async def test_ifindex_match_selects_correct_port_over_name_match(self):
        """When multiple vh ports exist, ifindex match wins over name heuristics."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=55),
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch(
                "agent.helpers._ovs_get_port_vlan",
                new_callable=AsyncMock,
                return_value=500,
            ),
        ):
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(
                return_value=(b"vhswap1\nvhswap2\nvhswap3\n", b"")
            )
            list_proc.returncode = 0

            # vhswap1 ifindex=10 (wrong)
            p1 = AsyncMock()
            p1.communicate = AsyncMock(return_value=(b"10\n", b""))
            p1.returncode = 0

            # vhswap2 ifindex=55 (correct)
            p2 = AsyncMock()
            p2.communicate = AsyncMock(return_value=(b"55\n", b""))
            p2.returncode = 0

            mock_exec.side_effect = [list_proc, p1, p2]

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr", "eth1")
            assert result is not None
            assert result[0] == "vhswap2"

    @pytest.mark.asyncio
    async def test_no_match_returns_none_preventing_wrong_port_use(self):
        """If no port has matching ifindex, return None rather than guessing."""
        with (
            patch("agent.helpers._resolve_ifindex_sync", return_value=99),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            list_proc = AsyncMock()
            list_proc.communicate = AsyncMock(return_value=(b"vhonly\n", b""))
            list_proc.returncode = 0

            p1 = AsyncMock()
            p1.communicate = AsyncMock(return_value=(b"88\n", b""))
            p1.returncode = 0

            mock_exec.side_effect = [list_proc, p1]

            from agent.helpers import _resolve_ovs_port_via_ifindex

            result = await _resolve_ovs_port_via_ifindex("ctr", "eth1")
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_ifindex_sync_returns_none_on_exec_failure(self):
        """_resolve_ifindex_sync should return None when docker exec fails."""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (1, b"")
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.helpers.get_docker_client", return_value=mock_client):
            from agent.helpers import _resolve_ifindex_sync

            result = _resolve_ifindex_sync("ctr", "eth1")
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_ifindex_sync_returns_int_on_success(self):
        """_resolve_ifindex_sync should return the parsed peer ifindex."""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"42\n")
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("agent.helpers.get_docker_client", return_value=mock_client):
            from agent.helpers import _resolve_ifindex_sync

            result = _resolve_ifindex_sync("ctr", "eth1")
            assert result == 42

    @pytest.mark.asyncio
    async def test_resolve_ifindex_sync_handles_exception_gracefully(self):
        """_resolve_ifindex_sync should return None on unexpected errors."""
        with patch(
            "agent.helpers.get_docker_client",
            side_effect=RuntimeError("no docker"),
        ):
            from agent.helpers import _resolve_ifindex_sync

            result = _resolve_ifindex_sync("ctr", "eth1")
            assert result is None
