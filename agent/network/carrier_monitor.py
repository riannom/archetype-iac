"""OVS carrier state monitor.

Polls OVS interface link_state and reports changes to the API controller.
When a NOS does ``shutdown`` on an interface (clearing IFF_UP), the host-side
veth peer carrier drops and OVS records ``link_state=down``.  This module
detects that transition and calls a notifier callback so the API can
propagate carrier off to the far-end peer.

The monitor tracks managed ports from **both** ``OVSNetworkManager._ports``
(VXLAN overlay, external) and ``DockerOVSPlugin.endpoints`` (container
interfaces).  VXLAN tunnels, management networks, and the OVS bridge
internal port are excluded.

Loop prevention: When the API propagates carrier off to the peer via
``ip link set carrier off`` inside the remote container, only IFLA_CARRIER
is changed (IFF_UP stays set), so the host-side veth stays up and OVS
never reports it as a transition — no loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitoredPort:
    """Lightweight carrier-monitor view of an OVS port."""

    port_name: str  # OVS port name (host-side veth)
    container_name: str  # Container/VM name
    interface_name: str  # Interface name inside container (e.g. "eth1")
    lab_id: str  # Lab this port belongs to


def build_managed_ports(
    ovs_mgr: Any | None = None,
    docker_plugin: Any | None = None,
) -> dict[str, MonitoredPort]:
    """Merge ports from OVSNetworkManager and DockerOVSPlugin.

    Returns ``{port_name: MonitoredPort}`` keyed by OVS port name.
    Docker plugin endpoints take precedence over OVS manager ports when
    both track the same port (shouldn't happen, but safe).
    """
    result: dict[str, MonitoredPort] = {}

    # 1. OVSNetworkManager._ports (keyed by container:iface, values are OVSPort)
    if ovs_mgr is not None:
        for port in ovs_mgr._ports.values():
            result[port.port_name] = MonitoredPort(
                port_name=port.port_name,
                container_name=port.container_name,
                interface_name=port.interface_name,
                lab_id=port.lab_id,
            )

    # 2. DockerOVSPlugin.endpoints (keyed by endpoint_id)
    #    Need to join with plugin.networks to get lab_id.
    if docker_plugin is not None:
        # Build network_id -> lab_id lookup
        net_to_lab: dict[str, str] = {}
        for net in docker_plugin.networks.values():
            net_to_lab[net.network_id] = net.lab_id

        for ep in docker_plugin.endpoints.values():
            if not ep.host_veth:
                continue  # Not yet provisioned
            lab_id = net_to_lab.get(ep.network_id, "")
            if not lab_id:
                continue  # Orphaned endpoint or management network
            result[ep.host_veth] = MonitoredPort(
                port_name=ep.host_veth,
                container_name=ep.container_name or "",
                interface_name=ep.interface_name,
                lab_id=lab_id,
            )

    return result


class CarrierMonitor:
    """Polls OVS port link_state and reports carrier changes to the API."""

    def __init__(
        self,
        ovs_bridge: str,
        get_managed_ports: Callable[[], dict[str, Any]],
        notifier: Callable[[str, str, str, str], Awaitable[bool]],
    ):
        """
        Args:
            ovs_bridge: OVS bridge name (e.g. "arch-ovs").
            get_managed_ports: Callable returning ``{port_name: MonitoredPort}``
                (or any object with ``.port_name``, ``.container_name``,
                ``.interface_name``, ``.lab_id``).  Called each poll cycle so
                newly added ports are picked up automatically.
            notifier: ``async fn(lab_id, node_name, interface, "on"|"off")``
                called when a managed port's link_state changes.
        """
        self._bridge = ovs_bridge
        self._get_managed_ports = get_managed_ports
        self._notifier = notifier
        self._last_link_states: dict[str, str] = {}  # ovs_port_name -> "up"/"down"
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, interval: float = 3.0) -> None:
        """Start polling in a background task."""
        if self._task is not None:
            return
        # Seed initial state so we don't fire spurious transitions.
        await self._seed_initial_state()
        self._task = asyncio.create_task(self._poll_loop(interval))
        logger.info(
            "CarrierMonitor started (bridge=%s, interval=%.1fs, tracked=%d ports)",
            self._bridge,
            interval,
            len(self._last_link_states),
        )

    def stop(self) -> None:
        """Cancel the background task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("CarrierMonitor stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _poll_loop(self, interval: float) -> None:
        """Run forever, polling OVS every *interval* seconds."""
        while True:
            try:
                await asyncio.sleep(interval)
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("CarrierMonitor poll error")

    async def _seed_initial_state(self) -> None:
        """Read current OVS link_state for all managed ports (no notifications)."""
        ovs_states = await self._query_ovs_link_states()
        managed_port_names = self._get_managed_port_names()
        for port_name, state in ovs_states.items():
            if port_name in managed_port_names:
                self._last_link_states[port_name] = state

    async def _poll_once(self) -> None:
        """Single poll cycle: detect transitions and notify."""
        ovs_states = await self._query_ovs_link_states()
        managed = self._get_managed_port_names()

        # Detect transitions
        notifications: list[tuple[str, str]] = []  # (port_name, new_state)

        for port_name in managed:
            current = ovs_states.get(port_name)
            if current is None:
                # Port disappeared from OVS — clean up tracking silently.
                self._last_link_states.pop(port_name, None)
                continue

            previous = self._last_link_states.get(port_name)
            if previous is None:
                # Newly managed port — record without notifying.
                self._last_link_states[port_name] = current
                continue

            if current != previous:
                self._last_link_states[port_name] = current
                notifications.append((port_name, current))

        # Prune ports that are no longer managed.
        stale = set(self._last_link_states) - managed
        for port_name in stale:
            self._last_link_states.pop(port_name, None)

        # Fire notifications (non-blocking, log errors).
        for port_name, new_state in notifications:
            carrier_state = "on" if new_state == "up" else "off"
            info = self._resolve_port(port_name)
            if info is None:
                continue
            lab_id, node_name, interface = info
            logger.info(
                "Carrier change detected: %s:%s %s (port=%s)",
                node_name,
                interface,
                carrier_state,
                port_name,
            )
            try:
                asyncio.create_task(
                    self._notifier(lab_id, node_name, interface, carrier_state)
                )
            except Exception:
                logger.exception(
                    "Failed to dispatch carrier notification for %s:%s",
                    node_name,
                    interface,
                )

    # ------------------------------------------------------------------
    # OVS query
    # ------------------------------------------------------------------

    async def _query_ovs_link_states(self) -> dict[str, str]:
        """Batch-query OVS for all interface link_state values.

        Returns ``{port_name: "up"|"down"}``.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ovs-vsctl",
                "--format=json",
                "--columns=name,link_state",
                "list",
                "Interface",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0 or not stdout:
                return {}

            data = json.loads(stdout.decode())
            result: dict[str, str] = {}
            for row in data.get("data", []):
                # Columns: name, link_state
                name = row[0]
                link_state = row[1]
                if isinstance(name, str) and isinstance(link_state, str):
                    result[name] = link_state
            return result
        except Exception:
            logger.exception("Failed to query OVS link_states")
            return {}

    # ------------------------------------------------------------------
    # Port resolution helpers
    # ------------------------------------------------------------------

    def _get_managed_port_names(self) -> set[str]:
        """Return the set of OVS port names that we should monitor."""
        return set(self._get_managed_ports().keys())

    def _resolve_port(self, port_name: str) -> tuple[str, str, str] | None:
        """Resolve an OVS port_name to (lab_id, node_name, interface).

        Returns None if the port is not managed or cannot be resolved.
        """
        port = self._get_managed_ports().get(port_name)
        if port is None:
            return None
        node_name = self._container_to_node(port.container_name, port.lab_id)
        return (port.lab_id, node_name, port.interface_name)

    @staticmethod
    def _container_to_node(container_name: str, lab_id: str) -> str:
        """Extract node name from container name.

        Container names follow ``archetype-{lab_id_prefix}-{node}`` or
        ``arch-{lab_id_prefix}-{node}`` patterns.
        """
        # Try archetype- prefix first (Docker containers)
        prefix = f"archetype-{lab_id[:20]}-"
        if container_name.startswith(prefix):
            return container_name[len(prefix):]

        # Try arch- prefix (libvirt VMs)
        prefix = f"arch-{lab_id[:20]}-"
        if container_name.startswith(prefix):
            return container_name[len(prefix):]

        # Fallback: return the container name as-is
        return container_name
