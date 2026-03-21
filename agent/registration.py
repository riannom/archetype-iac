"""Controller communication for the Archetype agent.

Handles agent registration, heartbeat loop, event forwarding,
and transport config bootstrap.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

import agent.agent_state as _state
from agent.config import settings
from agent.helpers import get_agent_info, get_resource_usage, get_docker_snapshotter_mode
from agent.http_client import get_controller_auth_headers, get_http_client
from agent.network.backends.registry import get_network_backend
from agent.schemas import (
    AgentStatus,
    HeartbeatRequest,
    HeartbeatResponse,
    RegistrationRequest,
    RegistrationResponse,
)

logger = logging.getLogger(__name__)


async def forward_event_to_controller(event) -> None:
    """Forward a node event to the controller.

    This function is called by the event listener when a container
    state change is detected. It POSTs the event to the controller's
    /events/node endpoint for real-time state synchronization.
    """
    from agent.events.base import NodeEvent, NodeEventType

    if not isinstance(event, NodeEvent):
        return

    # Handle container restart - reprovision OVS interfaces if needed
    if event.event_type == NodeEventType.STARTED:
        container_name = event.attributes.get("container_name") if event.attributes else None
        if container_name:
            try:
                backend = get_network_backend()
                await backend.handle_container_restart(container_name, event.lab_id)
            except Exception as e:
                logger.warning(f"Failed to reprovision interfaces for {container_name}: {e}")

    payload = {
        "agent_id": _state.AGENT_ID,
        "lab_id": event.lab_id,
        "node_name": event.node_name,
        "container_id": event.container_id,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.isoformat(),
        "status": event.status,
        "attributes": event.attributes,
    }

    try:
        client = get_http_client()
        response = await client.post(
            f"{settings.controller_url}/events/node",
            json=payload,
            timeout=5.0,
            headers=get_controller_auth_headers(),
        )
        if response.status_code == 200:
            logger.debug(f"Forwarded event: {event.event_type.value} for {event.log_name()}")
        else:
            logger.warning(f"Failed to forward event: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error forwarding event to controller: {e}")


async def register_with_controller() -> bool:
    """Register this agent with the controller."""
    request = RegistrationRequest(
        agent=get_agent_info(),
        token=settings.registration_token or None,
    )

    try:
        client = get_http_client()
        response = await client.post(
            f"{settings.controller_url}/agents/register",
            json=request.model_dump(mode='json'),
            timeout=settings.registration_timeout,
            headers=get_controller_auth_headers(),
        )
        if response.status_code == 200:
            result = RegistrationResponse(**response.json())
            if result.success:
                _state.set_registered(True)
                # Use the assigned ID from controller (may differ if we're
                # re-registering an existing agent with a new generated ID)
                if result.assigned_id and result.assigned_id != _state.AGENT_ID:
                    logger.info(f"Controller assigned existing ID: {result.assigned_id}")
                    _state.set_agent_id(result.assigned_id)
                logger.info(f"Registered with controller as {_state.AGENT_ID}")
                return True
            else:
                logger.warning(f"Registration rejected: {result.message}")
                return False
        else:
            body = response.text[:500] if response.text else "(empty)"
            logger.error(f"Registration failed: HTTP {response.status_code}: {body}")
            return False
    except httpx.ConnectError:
        logger.warning(f"Cannot connect to controller at {settings.controller_url}")
        return False
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return False


async def _bootstrap_transport_config() -> None:
    """Phase 2 bootstrap: fetch transport config from controller and apply.

    After registration, the agent fetches its transport configuration
    from the controller. If a subinterface or dedicated interface is
    configured, it provisions the interface locally and sets the
    data_plane_ip for VXLAN operations.
    """
    from agent.network.cmd import run_cmd as _async_run_cmd
    from agent.network.transport import set_data_plane_ip

    if not _state._registered:
        return

    async def _ip_cmd(*args: str) -> None:
        """Run an ip command asynchronously, raise on failure."""
        code, _, stderr = await _async_run_cmd(["ip", *args])
        if code != 0:
            raise RuntimeError(f"ip {' '.join(args)} failed: {stderr}")

    try:
        client = get_http_client()
        response = await client.get(
            f"{settings.controller_url}/infrastructure/agents/{_state.AGENT_ID}/transport-config",
            timeout=10.0,
            headers=get_controller_auth_headers(),
        )
        if response.status_code != 200:
            logger.debug("No transport config from controller (not configured)")
            return

        config = response.json()
        mode = config.get("transport_mode", "management")

        if mode == "management":
            logger.debug("Transport mode: management (no subinterface needed)")
            return

        if mode == "subinterface":
            parent = config.get("parent_interface")
            vlan_id = config.get("vlan_id")
            ip_cidr = config.get("transport_ip")
            mtu = config.get("desired_mtu", 9000)

            if not parent or not vlan_id:
                logger.warning("Subinterface transport config missing parent_interface or vlan_id")
                return

            iface_name = f"{parent}.{vlan_id}"
            logger.info(f"Bootstrap: provisioning transport subinterface {iface_name}")

            # Check if subinterface exists
            code, _, _ = await _async_run_cmd(["ip", "link", "show", iface_name])
            if code != 0:
                # Create the subinterface
                await _ip_cmd(
                    "link", "add", "link", parent,
                    "name", iface_name, "type", "vlan", "id", str(vlan_id),
                )

            # Set parent MTU if needed
            try:
                with open(f"/sys/class/net/{parent}/mtu") as f:
                    parent_mtu = int(f.read().strip())
                if parent_mtu < mtu:
                    await _ip_cmd("link", "set", parent, "mtu", str(mtu))
            except (FileNotFoundError, ValueError):
                pass

            # Set MTU, IP, bring up
            await _ip_cmd("link", "set", iface_name, "mtu", str(mtu))
            if ip_cidr:
                await _ip_cmd("addr", "flush", "dev", iface_name)
                await _ip_cmd("addr", "add", ip_cidr, "dev", iface_name)
            await _ip_cmd("link", "set", iface_name, "up")

            # Extract IP and set as data plane IP
            if ip_cidr:
                dp_ip = ip_cidr.split("/")[0]
                set_data_plane_ip(dp_ip)
                logger.info(f"Transport bootstrap complete: {iface_name} with IP {dp_ip}")

        elif mode == "dedicated":
            dp_iface = config.get("data_plane_interface")
            ip_cidr = config.get("transport_ip")
            mtu = config.get("desired_mtu", 9000)

            if not dp_iface:
                logger.warning("Dedicated transport config missing data_plane_interface")
                return

            logger.info(f"Bootstrap: configuring dedicated transport interface {dp_iface}")

            # Configure existing interface
            await _ip_cmd("link", "set", dp_iface, "mtu", str(mtu))
            if ip_cidr:
                await _ip_cmd("addr", "flush", "dev", dp_iface)
                await _ip_cmd("addr", "add", ip_cidr, "dev", dp_iface)
            await _ip_cmd("link", "set", dp_iface, "up")

            if ip_cidr:
                dp_ip = ip_cidr.split("/")[0]
                set_data_plane_ip(dp_ip)
                logger.info(f"Transport bootstrap complete: {dp_iface} with IP {dp_ip}")

    except httpx.ConnectError:
        logger.debug("Cannot reach controller for transport config (will retry on next heartbeat)")
    except Exception as e:
        logger.warning(f"Transport bootstrap failed: {e}")


async def send_heartbeat() -> HeartbeatResponse | None:
    """Send heartbeat to controller."""
    from agent.network.transport import get_data_plane_ip
    request = HeartbeatRequest(
        agent_id=_state.AGENT_ID,
        status=AgentStatus.ONLINE,
        active_jobs=_state.get_active_jobs(),
        resource_usage=await get_resource_usage(),
        data_plane_ip=get_data_plane_ip(),
        docker_snapshotter_mode=get_docker_snapshotter_mode(),
    )

    try:
        client = get_http_client()
        response = await client.post(
            f"{settings.controller_url}/agents/{_state.AGENT_ID}/heartbeat",
            json=request.model_dump(),
            timeout=settings.heartbeat_timeout,
            headers=get_controller_auth_headers(),
        )
        if response.status_code == 200:
            return HeartbeatResponse(**response.json())
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
    return None


async def heartbeat_loop() -> None:
    """Background task to send periodic heartbeats."""
    while True:
        await asyncio.sleep(settings.heartbeat_interval)

        if not _state._registered:
            # Try to register again
            await register_with_controller()
            continue

        response = await send_heartbeat()
        if response is None:
            # Controller unreachable, mark as unregistered to retry
            _state.set_registered(False)
            logger.warning("Lost connection to controller, will retry registration")
