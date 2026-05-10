"""Tests for agent/network/plugin_vlan.py — PluginVlanMixin behaviors.

Complementary coverage to test_plugin_vlan.py and test_docker_plugin_discovery.py.
Focuses on:
- Idempotency / determinism of hot-connect over repeated invocations
- Recovery paths (rebuild from OVS state after agent restart)
- Failure ordering (release allocation when OVS push fails)
- Stale-state handling (OVS port missing -> never return cached VLAN)
- Cross-lab isolation (refusing to wire endpoints whose networks span labs)
- Cleanup ordering (linked-tag release on byhostveth update)
- Boundary cases: VLAN tag out-of-range, malformed OVS output, no-op updates
"""
from __future__ import annotations

from unittest.mock import AsyncMock

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
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """Build a DockerOVSPlugin with workspace pinned to tmp_path.

    Mark-dirty-and-save is stubbed away so tests don't touch the real disk
    state file. _ovs_vsctl is left to per-test override.
    """
    monkeypatch.setattr(
        "agent.network.docker_plugin.settings.workspace_path", str(tmp_path)
    )
    p = DockerOVSPlugin()
    monkeypatch.setattr(p, "_mark_dirty_and_save", AsyncMock(return_value=None))
    # Default: no VLAN tags reported as already-used on the bridge
    monkeypatch.setattr(
        p, "_get_used_vlan_tags_on_bridge", AsyncMock(return_value=set())
    )
    return p


def _add_lab(p: DockerOVSPlugin, lab_id: str = "lab1") -> LabBridge:
    lb = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    p.lab_bridges[lab_id] = lb
    return lb


def _add_endpoint(
    p: DockerOVSPlugin,
    lab_id: str,
    endpoint_id: str,
    network_id: str,
    interface: str,
    host_veth: str,
    vlan_tag: int,
    container_name: str | None = None,
) -> EndpointState:
    ep = EndpointState(
        endpoint_id=endpoint_id,
        network_id=network_id,
        interface_name=interface,
        host_veth=host_veth,
        cont_veth=f"vc-{endpoint_id}",
        vlan_tag=vlan_tag,
        container_name=container_name,
    )
    p.endpoints[endpoint_id] = ep
    if network_id not in p.networks:
        p.networks[network_id] = NetworkState(
            network_id=network_id,
            lab_id=lab_id,
            interface_name=interface,
            bridge_name="arch-ovs",
        )
    return p.endpoints[endpoint_id]


# ===========================================================================
# Hot-Connect — determinism, idempotency, and cross-lab guards
# ===========================================================================


class TestHotConnectIdempotencyAndDeterminism:
    """Re-invocation produces stable port mapping; cross-lab is refused."""

    async def test_hot_connect_writes_same_tag_to_both_endpoints(self, plugin, monkeypatch):
        """Both ports must receive identical tag= argument on the same call."""
        _add_lab(plugin)
        ep_a = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        ep_b = _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        ovs_calls: list[tuple] = []

        async def fake_ovs(*args):
            ovs_calls.append(args)
            return 0, "", ""

        monkeypatch.setattr(plugin, "_ovs_vsctl", fake_ovs)

        shared = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert shared is not None
        # Both ports were targeted with the exact same tag value.
        set_calls = [c for c in ovs_calls if c[:2] == ("set", "port")]
        assert len(set_calls) == 2
        assert set_calls[0][2] == "vhA" and set_calls[0][3] == f"tag={shared}"
        assert set_calls[1][2] == "vhB" and set_calls[1][3] == f"tag={shared}"
        assert ep_a.vlan_tag == shared and ep_b.vlan_tag == shared

    async def test_hot_connect_replay_keeps_endpoints_on_a_shared_tag(self, plugin, monkeypatch):
        """Two consecutive hot_connects on the same pair leave them on a single shared tag.

        Idempotency at the *behavioral* level: after each call both endpoints share
        whatever tag is currently set, and the mapping survives without leaking the
        previous shared tag forever.
        """
        _add_lab(plugin)
        ep_a = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        ep_b = _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))

        v1 = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert ep_a.vlan_tag == v1 == ep_b.vlan_tag

        v2 = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert ep_a.vlan_tag == v2 == ep_b.vlan_tag
        # Old tag must have been released — only the current shared tag is allocated.
        assert v1 not in plugin._allocated_linked_vlans
        assert v2 in plugin._allocated_linked_vlans

    async def test_hot_connect_refuses_endpoints_in_different_labs(self, plugin, monkeypatch):
        """Networks belonging to a different lab must not be silently linked."""
        _add_lab(plugin, "lab1")
        _add_lab(plugin, "lab2")
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        # ep-b is on lab2's network even though we look it up under lab1
        _add_endpoint(plugin, "lab2", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    async def test_hot_connect_releases_tag_when_port_b_set_fails(self, plugin, monkeypatch):
        """If port B's tag set fails after port A succeeded, the linked tag is released."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth1", "vhB", 101, "r2")
        monkeypatch.setattr(plugin, "_validate_endpoint_exists", AsyncMock(return_value=True))

        # First set-port succeeds (port A), second fails (port B)
        results = iter([(0, "", ""), (1, "", "ovs error on B")])

        async def fake_ovs(*args):
            return next(results)

        monkeypatch.setattr(plugin, "_ovs_vsctl", fake_ovs)

        # Snapshot allocated linked vlans before the failing call
        prior_linked = set(plugin._allocated_linked_vlans)

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None
        # No new linked-range tag should remain allocated after rollback
        assert plugin._allocated_linked_vlans == prior_linked


# ===========================================================================
# Hot-Disconnect — failure paths and lock-aware error handling
# ===========================================================================


class TestHotDisconnectFailures:
    """OVS failures during hot_disconnect must not leave stale state."""

    async def test_hot_disconnect_returns_none_on_ovs_set_failure(self, plugin, monkeypatch):
        """If OVS set-port fails, hot_disconnect returns None and does not mutate vlan_tag."""
        _add_lab(plugin)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")
        original_tag = ep.vlan_tag

        monkeypatch.setattr(
            plugin, "_ovs_vsctl", AsyncMock(return_value=(1, "", "ovs busted"))
        )

        result = await plugin.hot_disconnect("lab1", "r1", "eth1")
        assert result is None
        # Endpoint vlan tag remains unchanged on failure
        assert ep.vlan_tag == original_tag

    async def test_hot_disconnect_uses_isolated_range_default(self, plugin, monkeypatch):
        """A fresh disconnect should pull from the isolated range (100-2049)."""
        _add_lab(plugin)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))

        new_vlan = await plugin.hot_disconnect("lab1", "r1", "eth1")
        assert new_vlan is not None
        assert VLAN_RANGE_START <= new_vlan <= VLAN_RANGE_END
        assert ep.vlan_tag == new_vlan


# ===========================================================================
# Recovery / discovery edge cases
# ===========================================================================


class TestEndpointVlanReadStaleness:
    """get_endpoint_vlan(read_from_ovs=True) never returns a stale cached VLAN."""

    async def test_returns_none_when_ovs_port_missing(self, plugin, monkeypatch):
        """OVS get failing ('no row') must return None, not the in-memory tag."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")

        # ovs-vsctl get fails (e.g. port deleted) -> guard returns None
        monkeypatch.setattr(
            plugin,
            "_run_cmd",
            AsyncMock(return_value=(1, "", "no row \"vhA\" in table Port")),
        )

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1", read_from_ovs=True)
        assert vlan is None

    async def test_returns_none_when_ovs_returns_unparseable_tag(self, plugin, monkeypatch):
        """If OVS output isn't an int, do not fall through to the cached value."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")

        # exit 0 but stdout is garbage -> int() fails -> None (not 150)
        monkeypatch.setattr(
            plugin, "_run_cmd", AsyncMock(return_value=(0, "not-a-number", ""))
        )

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1", read_from_ovs=True)
        assert vlan is None

    async def test_returns_none_when_endpoint_has_empty_host_veth(self, plugin, monkeypatch):
        """An endpoint with no host_veth is treated as stale under read_from_ovs."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "", 150, "r1")

        # _run_cmd should never be called; stub it to assert if it is
        run_cmd = AsyncMock(return_value=(0, "150\n", ""))
        monkeypatch.setattr(plugin, "_run_cmd", run_cmd)

        vlan = await plugin.get_endpoint_vlan("lab1", "r1", "eth1", read_from_ovs=True)
        assert vlan is None
        run_cmd.assert_not_awaited()


class TestGetEndpointHostVeth:
    """get_endpoint_host_veth uses tracked state first, then discovery."""

    async def test_returns_tracked_host_veth(self, plugin, monkeypatch):
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        # Discovery must NOT be needed when tracking exists
        discover = AsyncMock(return_value=None)
        monkeypatch.setattr(plugin, "_discover_endpoint", discover)

        host_veth = await plugin.get_endpoint_host_veth("lab1", "r1", "eth1")
        assert host_veth == "vhA"
        discover.assert_not_awaited()

    async def test_falls_back_to_discovery_when_untracked(self, plugin, monkeypatch):
        """When no tracked endpoint matches, falls through to _discover_endpoint."""
        _add_lab(plugin)
        # Note: no endpoint with matching container_name/interface
        discovered = EndpointState(
            endpoint_id="ep-x",
            network_id="net-x",
            interface_name="eth1",
            host_veth="vhDiscovered",
            cont_veth="vc",
            vlan_tag=100,
            container_name="r1",
        )
        monkeypatch.setattr(
            plugin, "_discover_endpoint", AsyncMock(return_value=discovered)
        )

        host_veth = await plugin.get_endpoint_host_veth("lab1", "r1", "eth1")
        assert host_veth == "vhDiscovered"

    async def test_returns_none_when_neither_tracked_nor_discoverable(self, plugin, monkeypatch):
        _add_lab(plugin)
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        host_veth = await plugin.get_endpoint_host_veth("lab1", "r1", "eth1")
        assert host_veth is None

    async def test_skips_endpoints_belonging_to_other_lab(self, plugin, monkeypatch):
        """A tracked endpoint whose network belongs to another lab is ignored."""
        _add_lab(plugin, "lab1")
        _add_lab(plugin, "lab2")
        # endpoint exists but its network is registered under lab2
        _add_endpoint(plugin, "lab2", "ep-other", "net-other", "eth1", "vhOther", 100, "r1")
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        host_veth = await plugin.get_endpoint_host_veth("lab1", "r1", "eth1")
        assert host_veth is None


# ===========================================================================
# Container-name association
# ===========================================================================


class TestSetEndpointContainerName:
    """set_endpoint_container_name attaches container metadata for later lookups."""

    async def test_idempotent_on_repeated_calls(self, plugin):
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100)
        await plugin.set_endpoint_container_name("ep-a", "r1")
        await plugin.set_endpoint_container_name("ep-a", "r1")
        assert ep.container_name == "r1"

    async def test_overwrites_previous_container_name(self, plugin):
        """When containers are renamed/recreated, the new name wins."""
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "old")
        await plugin.set_endpoint_container_name("ep-a", "new")
        assert ep.container_name == "new"

    async def test_unknown_endpoint_silently_ignored(self, plugin):
        """No raise, no save — just a no-op for an unknown endpoint id."""
        # _mark_dirty_and_save is stubbed on the plugin fixture
        await plugin.set_endpoint_container_name("ep-does-not-exist", "r1")
        plugin._mark_dirty_and_save.assert_not_awaited()


# ===========================================================================
# set_endpoint_vlan_by_host_veth — range-aware bookkeeping
# ===========================================================================


class TestSetVlanByHostVethRangeBookkeeping:
    """Updating by host veth must keep the per-range allocation sets accurate."""

    async def test_isolated_to_linked_transition_releases_isolated_and_allocates_linked(
        self, plugin, monkeypatch
    ):
        """Transition from isolated tag to linked tag swaps allocation buckets."""
        _add_lab(plugin)
        old_tag = VLAN_RANGE_START + 5  # isolated range
        new_tag = LINKED_VLAN_START + 7  # linked range
        plugin._allocated_vlans.add(old_tag)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", old_tag, "r1")

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhA", new_tag)
        assert result is True
        assert ep.vlan_tag == new_tag
        assert old_tag not in plugin._allocated_vlans
        assert new_tag in plugin._allocated_linked_vlans
        assert new_tag not in plugin._allocated_vlans

    async def test_out_of_range_tag_is_recorded_but_not_tracked(self, plugin, monkeypatch):
        """A tag outside both ranges (e.g. a fragment from external vlan) is set
        on the endpoint but does not pollute either allocation bucket.
        """
        _add_lab(plugin)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 150, "r1")
        plugin._allocated_vlans.add(150)

        # Use a value out of *both* ranges to exercise the no-bucket path.
        weird_tag = LINKED_VLAN_END + 50
        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhA", weird_tag)
        assert result is True
        assert ep.vlan_tag == weird_tag
        # Old tag was released, but the new (out-of-range) tag is in neither set
        assert 150 not in plugin._allocated_vlans
        assert weird_tag not in plugin._allocated_vlans
        assert weird_tag not in plugin._allocated_linked_vlans

    async def test_no_op_when_tag_unchanged_does_not_save(self, plugin):
        """Setting the same tag back is a fast no-op — no save round-trip."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2060, "r1")

        result = await plugin.set_endpoint_vlan_by_host_veth("lab1", "vhA", 2060)
        assert result is True
        plugin._mark_dirty_and_save.assert_not_awaited()


# ===========================================================================
# get_container_interface_mapping — fallback to network definitions
# ===========================================================================


class TestContainerInterfaceMappingFallback:
    """When tracked endpoints are absent, mapping is derived from networks."""

    def test_mapping_from_endpoints_takes_precedence(self, plugin):
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "r1")

        mapping = plugin.get_container_interface_mapping("lab1", "r1")
        # Endpoint-derived mapping uses real veth names
        assert mapping == {"vhA": "eth1", "vhB": "eth2"}

    def test_mapping_falls_back_to_networks_when_endpoints_missing(self, plugin):
        """After agent restart with lost endpoint state, networks still describe intent."""
        _add_lab(plugin)
        # Register networks but no endpoints
        plugin.networks["net-a"] = NetworkState(
            network_id="net-a", lab_id="lab1", interface_name="eth1", bridge_name="arch-ovs"
        )
        plugin.networks["net-b"] = NetworkState(
            network_id="net-b", lab_id="lab1", interface_name="eth2", bridge_name="arch-ovs"
        )

        mapping = plugin.get_container_interface_mapping("lab1", "r1")
        # Fallback uses synthetic "network:<id>" keys per the implementation contract
        assert mapping == {"network:net-a": "eth1", "network:net-b": "eth2"}

    def test_mapping_skips_other_lab_networks_in_fallback(self, plugin):
        """Fallback derivation must filter out other labs' networks."""
        _add_lab(plugin, "lab1")
        plugin.networks["net-a"] = NetworkState(
            network_id="net-a", lab_id="lab1", interface_name="eth1", bridge_name="arch-ovs"
        )
        plugin.networks["net-z"] = NetworkState(
            network_id="net-z", lab_id="other-lab", interface_name="eth1", bridge_name="arch-ovs"
        )

        mapping = plugin.get_container_interface_mapping("lab1", "r1")
        assert mapping == {"network:net-a": "eth1"}

    def test_mapping_only_includes_matching_or_unset_container(self, plugin):
        """Endpoints attached to a different container are excluded; unset ones included."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "other-r")
        _add_endpoint(plugin, "lab1", "ep-c", "net-c", "eth3", "vhC", 102, container_name=None)

        mapping = plugin.get_container_interface_mapping("lab1", "r1")
        assert "vhA" in mapping  # matching container
        assert "vhB" not in mapping  # different container -> excluded
        assert "vhC" in mapping  # unset container -> included


# ===========================================================================
# Lab-status reporting
# ===========================================================================


class TestLabStatusReporting:
    """get_lab_status / get_all_labs / get_active_host_veths surface plugin state."""

    def test_lab_status_truncates_endpoint_and_network_ids(self, plugin):
        """Status strings should use 12-char id prefixes per the schema convention."""
        lb = _add_lab(plugin)
        long_net = "net-" + "a" * 32
        long_ep = "ep-" + "b" * 32
        lb.network_ids.add(long_net)
        plugin.networks[long_net] = NetworkState(
            network_id=long_net, lab_id="lab1", interface_name="eth1", bridge_name="arch-ovs"
        )
        plugin.endpoints[long_ep] = EndpointState(
            endpoint_id=long_ep,
            network_id=long_net,
            interface_name="eth1",
            host_veth="vhX",
            cont_veth="vcX",
            vlan_tag=100,
            container_name="r1",
        )

        status = plugin.get_lab_status("lab1")
        assert status is not None
        assert len(status["networks"][0]["network_id"]) == 12
        assert len(status["endpoints"][0]["endpoint_id"]) == 12
        # Truncation must preserve a real prefix, not be empty
        assert status["networks"][0]["network_id"] == long_net[:12]
        assert status["endpoints"][0]["endpoint_id"] == long_ep[:12]

    def test_lab_status_omits_other_labs_endpoints(self, plugin):
        """Endpoints whose network belongs to another lab are not surfaced."""
        _add_lab(plugin, "lab1")
        _add_lab(plugin, "lab2")
        _add_endpoint(plugin, "lab1", "ep-1", "net-1", "eth1", "vh1", 100, "r1")
        _add_endpoint(plugin, "lab2", "ep-2", "net-2", "eth1", "vh2", 100, "r2")

        status = plugin.get_lab_status("lab1")
        assert len(status["endpoints"]) == 1
        assert status["endpoints"][0]["host_veth"] == "vh1"

    def test_get_all_labs_empty(self, plugin):
        assert plugin.get_all_labs() == []

    def test_get_active_host_veths_empty(self, plugin):
        assert plugin.get_active_host_veths() == set()

    def test_get_active_host_veths_excludes_duplicates(self, plugin):
        """A host_veth shared across endpoints (shouldn't happen but be defensive)
        appears only once in the returned set.
        """
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhSAME", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhSAME", 101, "r1")
        assert plugin.get_active_host_veths() == {"vhSAME"}


# ===========================================================================
# _prune_stale_container_endpoints
# ===========================================================================


class TestPruneStaleContainerEndpoints:
    """Used by _discover_endpoint when Docker NotFound on a previously-tracked container."""

    async def test_prune_zero_when_no_match(self, plugin):
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")

        pruned = await plugin._prune_stale_container_endpoints("lab1", "different-container")
        assert pruned == 0
        assert "ep-a" in plugin.endpoints

    async def test_prune_does_not_save_when_nothing_removed(self, plugin):
        """No-op prune must not trigger a state save."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        await plugin._prune_stale_container_endpoints("lab1", "ghost")
        plugin._mark_dirty_and_save.assert_not_awaited()

    async def test_prune_returns_count_of_removed_endpoints(self, plugin):
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 100, "r1")
        _add_endpoint(plugin, "lab1", "ep-b", "net-b", "eth2", "vhB", 101, "r1")
        _add_endpoint(plugin, "lab1", "ep-c", "net-c", "eth3", "vhC", 102, "r2")

        pruned = await plugin._prune_stale_container_endpoints("lab1", "r1")
        assert pruned == 2
        assert "ep-c" in plugin.endpoints
        plugin._mark_dirty_and_save.assert_awaited_once()


# ===========================================================================
# set_endpoint_vlan — discovery fallback
# ===========================================================================


class TestSetEndpointVlanDiscoveryFallback:
    """When the endpoint isn't tracked, set_endpoint_vlan must try discovery."""

    async def test_discovers_and_records_when_untracked(self, plugin, monkeypatch):
        """If discovery succeeds, the endpoint is added to tracking with the new VLAN."""
        _add_lab(plugin)
        discovered = EndpointState(
            endpoint_id="ep-discovered",
            network_id="net-x",
            interface_name="eth1",
            host_veth="vhDisc",
            cont_veth="vcDisc",
            vlan_tag=999,
            container_name="r1",
        )
        monkeypatch.setattr(
            plugin, "_discover_endpoint", AsyncMock(return_value=discovered)
        )

        result = await plugin.set_endpoint_vlan("lab1", "r1", "eth1", 250)
        assert result is True
        # New VLAN was applied
        assert plugin.endpoints["ep-discovered"].vlan_tag == 250

    async def test_returns_false_when_neither_tracked_nor_discoverable(self, plugin, monkeypatch):
        _add_lab(plugin)
        monkeypatch.setattr(plugin, "_discover_endpoint", AsyncMock(return_value=None))

        result = await plugin.set_endpoint_vlan("lab1", "r1", "eth1", 250)
        assert result is False


# ===========================================================================
# Carrier-state shaping (input validation matrix)
# ===========================================================================


class TestCarrierStateValidation:
    """Carrier state accepts only 'on'/'off' — exhaustively verify rejection."""

    @pytest.mark.parametrize(
        "bad_state",
        ["", "ON", "OFF", "up", "down", "1", "0", "true", "false", "yes", "no"],
    )
    async def test_rejects_invalid_states(self, plugin, monkeypatch, bad_state):
        """Anything other than literal 'on'/'off' must be refused before nsenter runs."""
        # _get_container_pid should never be called — patch it to assert
        get_pid = AsyncMock(return_value=1234)
        monkeypatch.setattr(plugin, "_get_container_pid", get_pid)

        result = await plugin.set_carrier_state("lab1", "r1", "eth1", bad_state)
        assert result is False
        get_pid.assert_not_awaited()


# ===========================================================================
# restore_port — VLAN/carrier ordering
# ===========================================================================


class TestRestorePortOrdering:
    """restore_port writes the VLAN before bringing the carrier up."""

    async def test_vlan_set_failure_skips_carrier_call(self, plugin, monkeypatch):
        """If the OVS tag set fails, the carrier set must not be invoked."""
        _add_lab(plugin)
        _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 999, "r1")
        monkeypatch.setattr(
            plugin, "_ovs_vsctl", AsyncMock(return_value=(1, "", "ovs no"))
        )
        carrier = AsyncMock(return_value=True)
        monkeypatch.setattr(plugin, "set_carrier_state", carrier)

        result = await plugin.restore_port("lab1", "r1", "eth1", 2050)
        assert result is False
        carrier.assert_not_awaited()

    async def test_endpoint_vlan_updated_before_carrier_call(self, plugin, monkeypatch):
        """VLAN tag is updated in-memory before the carrier-on side-effect runs."""
        _add_lab(plugin)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 999, "r1")
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))

        observed_vlan_at_carrier_call: list[int] = []

        async def fake_carrier(_lab, _container, _iface, _state):
            observed_vlan_at_carrier_call.append(ep.vlan_tag)
            return True

        monkeypatch.setattr(plugin, "set_carrier_state", fake_carrier)

        result = await plugin.restore_port("lab1", "r1", "eth1", 2050)
        assert result is True
        # By the time carrier was set, the endpoint already reflected the new VLAN
        assert observed_vlan_at_carrier_call == [2050]


# ===========================================================================
# isolate_port — VLAN-first ordering
# ===========================================================================


class TestIsolatePortOrdering:
    """isolate_port runs hot_disconnect (OVS) before nsenter carrier off."""

    async def test_carrier_failure_does_not_revert_vlan_change(self, plugin, monkeypatch):
        """Even if carrier off fails, the new isolated VLAN remains assigned."""
        _add_lab(plugin)
        ep = _add_endpoint(plugin, "lab1", "ep-a", "net-a", "eth1", "vhA", 2050, "r1")
        monkeypatch.setattr(plugin, "_ovs_vsctl", AsyncMock(return_value=(0, "", "")))
        # carrier off fails
        monkeypatch.setattr(
            plugin, "set_carrier_state", AsyncMock(return_value=False)
        )

        new_vlan = await plugin.isolate_port("lab1", "r1", "eth1")
        assert new_vlan is not None
        # The VLAN-based isolation already took effect, so endpoint still has the new tag
        assert ep.vlan_tag == new_vlan

    async def test_hot_disconnect_failure_short_circuits_before_carrier(self, plugin, monkeypatch):
        """If hot_disconnect returns None (e.g. unknown lab), carrier is never touched."""
        # No lab bridge -> hot_disconnect returns None
        carrier = AsyncMock(return_value=True)
        monkeypatch.setattr(plugin, "set_carrier_state", carrier)

        result = await plugin.isolate_port("nope", "r1", "eth1")
        assert result is None
        carrier.assert_not_awaited()
