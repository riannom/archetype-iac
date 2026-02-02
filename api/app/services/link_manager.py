"""Link Manager Service.

This service handles link connectivity operations including:
- Same-host link creation (OVS VLAN tag matching)
- Cross-host link creation (VXLAN tunnels)
- Link state enforcement (up/down via carrier state)
- Link teardown

The service coordinates between the API and agents to establish
L2 connectivity between container interfaces.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import agent_client, models

logger = logging.getLogger(__name__)


def allocate_vni(lab_id: str, link_name: str) -> int:
    """Allocate a deterministic VNI for a cross-host link.

    Uses MD5 hash of lab_id:link_name to ensure:
    - Same link always gets same VNI (idempotent)
    - Different links get different VNIs (collision-resistant)

    VNI range: 1000-16001000 (avoids reserved low values)

    Args:
        lab_id: Lab identifier
        link_name: Link identifier (e.g., "node1:eth1-node2:eth1")

    Returns:
        VNI in valid range
    """
    combined = f"{lab_id}:{link_name}"
    hash_val = int(hashlib.md5(combined.encode()).hexdigest()[:8], 16)
    return (hash_val % 16000000) + 1000


class LinkManager:
    """Manages link connectivity between containers.

    This class provides high-level operations for creating and destroying
    network links, abstracting the difference between same-host links
    (OVS VLAN tags) and cross-host links (VXLAN tunnels).
    """

    def __init__(self, session: Session):
        self.session = session

    async def connect_link(
        self,
        link_state: models.LinkState,
        agents: dict[str, models.Host],
    ) -> bool:
        """Establish L2 connectivity for a link.

        For same-host links, uses OVS hot_connect to match VLAN tags.
        For cross-host links, creates VXLAN tunnel and then hot_connects.

        Args:
            link_state: The LinkState record to connect
            agents: Dict mapping host_id to Host objects

        Returns:
            True if link was connected successfully
        """
        lab_id = link_state.lab_id

        # Determine which hosts have the source and target nodes
        source_host_id = link_state.source_host_id
        target_host_id = link_state.target_host_id

        if not source_host_id or not target_host_id:
            # Look up from NodePlacement or Node records
            source_host_id, target_host_id = self._lookup_endpoint_hosts(link_state)

        if not source_host_id or not target_host_id:
            logger.error(f"Cannot determine hosts for link {link_state.link_name}")
            link_state.actual_state = "error"
            link_state.error_message = "Cannot determine endpoint hosts"
            return False

        # Store host IDs in link_state
        link_state.source_host_id = source_host_id
        link_state.target_host_id = target_host_id

        # Check if same-host or cross-host
        is_cross_host = source_host_id != target_host_id
        link_state.is_cross_host = is_cross_host

        if is_cross_host:
            return await self._connect_cross_host_link(link_state, agents)
        else:
            return await self._connect_same_host_link(link_state, agents)

    async def disconnect_link(
        self,
        link_state: models.LinkState,
        agents: dict[str, models.Host],
    ) -> bool:
        """Disconnect a link by isolating endpoints and setting carrier off.

        Args:
            link_state: The LinkState record to disconnect
            agents: Dict mapping host_id to Host objects

        Returns:
            True if link was disconnected successfully
        """
        success = True

        # Isolate source endpoint
        source_agent = agents.get(link_state.source_host_id)
        if source_agent:
            result = await self._isolate_endpoint(
                source_agent,
                link_state.lab_id,
                link_state.source_node,
                link_state.source_interface,
            )
            if result:
                link_state.source_carrier_state = "off"
            else:
                success = False

        # Isolate target endpoint
        target_agent = agents.get(link_state.target_host_id)
        if target_agent:
            result = await self._isolate_endpoint(
                target_agent,
                link_state.lab_id,
                link_state.target_node,
                link_state.target_interface,
            )
            if result:
                link_state.target_carrier_state = "off"
            else:
                success = False

        if success:
            link_state.actual_state = "down"
            link_state.error_message = None
        else:
            link_state.actual_state = "error"
            link_state.error_message = "Failed to isolate one or more endpoints"

        return success

    async def create_cross_host_link(
        self,
        link_state: models.LinkState,
        agent_a: models.Host,
        agent_b: models.Host,
    ) -> bool:
        """Create a VXLAN tunnel for a cross-host link.

        This creates the VXLAN infrastructure on both agents but does not
        connect the containers - use connect_link for that.

        Args:
            link_state: The LinkState record
            agent_a: Agent hosting the source endpoint
            agent_b: Agent hosting the target endpoint

        Returns:
            True if tunnel was created successfully
        """
        lab_id = link_state.lab_id

        # Allocate deterministic VNI
        vni = allocate_vni(lab_id, link_state.link_name)
        link_state.vni = vni

        # Get agent IP addresses
        agent_ip_a = self._extract_agent_ip(agent_a)
        agent_ip_b = self._extract_agent_ip(agent_b)

        # Create VXLAN tunnel on both agents
        result = await agent_client.setup_cross_host_link(
            database=self.session,
            lab_id=lab_id,
            link_id=link_state.link_name,
            agent_a=agent_a,
            agent_b=agent_b,
            node_a=link_state.source_node,
            interface_a=link_state.source_interface,
            node_b=link_state.target_node,
            interface_b=link_state.target_interface,
            vni=vni,
        )

        if result.get("success"):
            # Create VxlanTunnel record
            tunnel = models.VxlanTunnel(
                lab_id=lab_id,
                link_state_id=link_state.id,
                vni=vni,
                vlan_tag=result.get("vlan_tag", 0),
                agent_a_id=agent_a.id,
                agent_a_ip=agent_ip_a,
                agent_b_id=agent_b.id,
                agent_b_ip=agent_ip_b,
                status="active",
            )
            self.session.add(tunnel)

            link_state.vlan_tag = result.get("vlan_tag")
            return True
        else:
            link_state.actual_state = "error"
            link_state.error_message = result.get("error", "VXLAN tunnel creation failed")
            return False

    async def teardown_cross_host_link(
        self,
        link_state: models.LinkState,
        agent_a: models.Host,
        agent_b: models.Host,
    ) -> bool:
        """Tear down a VXLAN tunnel for a cross-host link.

        Args:
            link_state: The LinkState record
            agent_a: Agent hosting the source endpoint
            agent_b: Agent hosting the target endpoint

        Returns:
            True if tunnel was torn down successfully
        """
        lab_id = link_state.lab_id
        vni = link_state.vni

        if not vni:
            logger.warning(f"No VNI for link {link_state.link_name}, nothing to tear down")
            return True

        success = True

        # Delete VXLAN tunnel on both agents
        try:
            result_a = await agent_client.cleanup_overlay_on_agent(agent_a, lab_id)
            if result_a.get("errors"):
                logger.warning(f"Errors cleaning overlay on agent_a: {result_a['errors']}")
        except Exception as e:
            logger.error(f"Failed to clean overlay on agent_a: {e}")
            success = False

        try:
            result_b = await agent_client.cleanup_overlay_on_agent(agent_b, lab_id)
            if result_b.get("errors"):
                logger.warning(f"Errors cleaning overlay on agent_b: {result_b['errors']}")
        except Exception as e:
            logger.error(f"Failed to clean overlay on agent_b: {e}")
            success = False

        # Update VxlanTunnel record
        tunnel = (
            self.session.query(models.VxlanTunnel)
            .filter(
                models.VxlanTunnel.link_state_id == link_state.id,
            )
            .first()
        )
        if tunnel:
            tunnel.status = "cleanup" if success else "failed"

        # Clear VXLAN fields from link_state
        link_state.vni = None
        link_state.vlan_tag = None

        return success

    async def set_endpoint_carrier(
        self,
        link_state: models.LinkState,
        endpoint: str,
        state: str,
        agents: dict[str, models.Host],
    ) -> bool:
        """Set carrier state for one endpoint of a link.

        Args:
            link_state: The LinkState record
            endpoint: "source" or "target"
            state: "on" or "off"
            agents: Dict mapping host_id to Host objects

        Returns:
            True if carrier state was set successfully
        """
        if endpoint == "source":
            host_id = link_state.source_host_id
            node = link_state.source_node
            interface = link_state.source_interface
        elif endpoint == "target":
            host_id = link_state.target_host_id
            node = link_state.target_node
            interface = link_state.target_interface
        else:
            logger.error(f"Invalid endpoint: {endpoint}")
            return False

        agent = agents.get(host_id)
        if not agent:
            logger.error(f"Agent not found for host_id {host_id}")
            return False

        result = await self._set_carrier_state(agent, link_state.lab_id, node, interface, state)

        if result:
            if endpoint == "source":
                link_state.source_carrier_state = state
            else:
                link_state.target_carrier_state = state

        return result

    async def enforce_link_state(
        self,
        link_state: models.LinkState,
        agents: dict[str, models.Host],
    ) -> bool:
        """Enforce the desired state for a link.

        If desired_state is "up", connects the link and enables carrier.
        If desired_state is "down", disconnects the link and disables carrier.

        Args:
            link_state: The LinkState record to enforce
            agents: Dict mapping host_id to Host objects

        Returns:
            True if state was enforced successfully
        """
        if link_state.desired_state == "up":
            return await self.connect_link(link_state, agents)
        elif link_state.desired_state == "down":
            return await self.disconnect_link(link_state, agents)
        else:
            logger.warning(f"Unknown desired_state: {link_state.desired_state}")
            return False

    # =========================================================================
    # Private methods
    # =========================================================================

    def _lookup_endpoint_hosts(
        self,
        link_state: models.LinkState,
    ) -> tuple[str | None, str | None]:
        """Look up which hosts have the source and target nodes.

        First checks Node.host_id (explicit placement), then NodePlacement
        (runtime placement tracking).

        Returns:
            Tuple of (source_host_id, target_host_id)
        """
        lab_id = link_state.lab_id

        source_host_id = None
        target_host_id = None

        # Check Node.host_id first (explicit placement)
        source_node = (
            self.session.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == link_state.source_node,
            )
            .first()
        )
        if source_node and source_node.host_id:
            source_host_id = source_node.host_id

        target_node = (
            self.session.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == link_state.target_node,
            )
            .first()
        )
        if target_node and target_node.host_id:
            target_host_id = target_node.host_id

        # Fall back to NodePlacement
        if not source_host_id:
            placement = (
                self.session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == link_state.source_node,
                )
                .first()
            )
            if placement:
                source_host_id = placement.host_id

        if not target_host_id:
            placement = (
                self.session.query(models.NodePlacement)
                .filter(
                    models.NodePlacement.lab_id == lab_id,
                    models.NodePlacement.node_name == link_state.target_node,
                )
                .first()
            )
            if placement:
                target_host_id = placement.host_id

        return source_host_id, target_host_id

    def _extract_agent_ip(self, agent: models.Host) -> str:
        """Extract IP address from agent's address field."""
        addr = agent.address.replace("http://", "").replace("https://", "")
        return addr.split(":")[0]

    async def _connect_same_host_link(
        self,
        link_state: models.LinkState,
        agents: dict[str, models.Host],
    ) -> bool:
        """Connect a link where both endpoints are on the same host.

        Uses OVS hot_connect to put both interfaces in the same VLAN.
        """
        agent = agents.get(link_state.source_host_id)
        if not agent:
            link_state.actual_state = "error"
            link_state.error_message = f"Agent not found for host {link_state.source_host_id}"
            return False

        # Use agent's hot_connect endpoint
        result = await agent_client.create_link_on_agent(
            agent,
            lab_id=link_state.lab_id,
            source_node=link_state.source_node,
            source_interface=link_state.source_interface,
            target_node=link_state.target_node,
            target_interface=link_state.target_interface,
        )

        if result.get("success"):
            link_state.vlan_tag = result.get("vlan_tag")
            link_state.actual_state = "up"
            link_state.source_carrier_state = "on"
            link_state.target_carrier_state = "on"
            link_state.error_message = None
            return True
        else:
            link_state.actual_state = "error"
            link_state.error_message = result.get("error", "hot_connect failed")
            return False

    async def _connect_cross_host_link(
        self,
        link_state: models.LinkState,
        agents: dict[str, models.Host],
    ) -> bool:
        """Connect a link where endpoints are on different hosts.

        Creates VXLAN tunnel and then connects interfaces.
        """
        agent_a = agents.get(link_state.source_host_id)
        agent_b = agents.get(link_state.target_host_id)

        if not agent_a or not agent_b:
            link_state.actual_state = "error"
            link_state.error_message = "One or more agents not available"
            return False

        # Create VXLAN tunnel
        success = await self.create_cross_host_link(link_state, agent_a, agent_b)

        if success:
            link_state.actual_state = "up"
            link_state.source_carrier_state = "on"
            link_state.target_carrier_state = "on"
            link_state.error_message = None

        return success

    async def _isolate_endpoint(
        self,
        agent: models.Host,
        lab_id: str,
        node: str,
        interface: str,
    ) -> bool:
        """Isolate a single endpoint via VLAN and carrier off."""
        try:
            url = f"http://{agent.address}/labs/{lab_id}/interfaces/{node}/{interface}/isolate"
            client = agent_client.get_http_client()
            response = await client.post(url, timeout=10.0)
            if response.status_code == 200:
                result = response.json()
                return result.get("success", False)
            else:
                logger.error(f"Isolate endpoint failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to isolate endpoint {node}:{interface}: {e}")
            return False

    async def _set_carrier_state(
        self,
        agent: models.Host,
        lab_id: str,
        node: str,
        interface: str,
        state: str,
    ) -> bool:
        """Set carrier state on an endpoint."""
        try:
            url = f"http://{agent.address}/labs/{lab_id}/interfaces/{node}/{interface}/carrier"
            client = agent_client.get_http_client()
            response = await client.post(
                url,
                json={"state": state},
                timeout=10.0,
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("success", False)
            else:
                logger.error(f"Set carrier failed: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to set carrier {state} on {node}:{interface}: {e}")
            return False
