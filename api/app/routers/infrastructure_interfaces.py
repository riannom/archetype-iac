"""Agent interface configuration, network config, and managed interface endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import agent_client, db, models
from app.auth import get_current_admin, get_current_user
from app.utils.http import require_admin, raise_not_found
from app.schemas import (
    InterfaceDetailsResponseOut,
    SetMtuRequestIn,
    SetMtuResponseOut,
    AgentNetworkConfigOut,
    AgentNetworkConfigUpdate,
    AgentManagedInterfaceOut,
    AgentManagedInterfaceCreate,
    AgentManagedInterfaceUpdate,
    AgentManagedInterfacesResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Agent Interface Configuration Endpoints
# =============================================================================


@router.get("/agents/{agent_id}/interfaces", response_model=InterfaceDetailsResponseOut)
async def get_agent_interfaces(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> InterfaceDetailsResponseOut:
    """Get detailed interface information from an agent.

    Returns all interfaces with their MTU, identifies the default route
    interface, and detects which network manager is in use on the agent.
    """

    # Get the agent
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    if not agent_client.is_agent_online(agent):
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        result = await agent_client.get_agent_interface_details(agent)
        normalized_interfaces = []
        default_route = result.get("default_route_interface")
        for iface in result.get("interfaces", []):
            iface_data = dict(iface)
            iface_data.setdefault("is_physical", True)
            iface_data.setdefault("is_default_route", iface_data.get("name") == default_route)
            iface_data.setdefault("state", "unknown")
            normalized_interfaces.append(iface_data)
        result = {
            **result,
            "interfaces": normalized_interfaces,
        }
        return InterfaceDetailsResponseOut(**result)
    except Exception as e:
        logger.error(f"Failed to get interfaces from agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{agent_id}/interfaces/{interface_name}/mtu", response_model=SetMtuResponseOut)
async def set_agent_interface_mtu(
    agent_id: str,
    interface_name: str,
    request: SetMtuRequestIn,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> SetMtuResponseOut:
    """Set MTU on an agent's interface.

    Requires admin access. Applies the MTU change and optionally persists
    it across reboots (based on detected network manager).
    """

    require_admin(current_user)

    # Get the agent
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    if not agent_client.is_agent_online(agent):
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        result = await agent_client.set_agent_interface_mtu(
            agent, interface_name, request.mtu, request.persist
        )
        return SetMtuResponseOut(**result)
    except Exception as e:
        logger.error(f"Failed to set MTU on agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_id}/network-config", response_model=AgentNetworkConfigOut)
def get_agent_network_config(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentNetworkConfigOut:
    """Get the network configuration for an agent.

    Returns the configured data plane interface and desired MTU, along
    with the last known actual MTU and sync status.
    """
    # Get the agent
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    # Get or create network config
    config = (
        database.query(models.AgentNetworkConfig)
        .filter(models.AgentNetworkConfig.host_id == agent_id)
        .first()
    )

    if not config:
        # Create default config
        import uuid
        config = models.AgentNetworkConfig(
            id=str(uuid.uuid4()),
            host_id=agent_id,
        )
        database.add(config)
        database.commit()
        database.refresh(config)

    return AgentNetworkConfigOut(
        id=config.id,
        host_id=config.host_id,
        host_name=agent.name,
        data_plane_interface=config.data_plane_interface,
        desired_mtu=config.desired_mtu,
        current_mtu=config.current_mtu,
        last_sync_at=config.last_sync_at,
        sync_status=config.sync_status,
        sync_error=config.sync_error,
    )


@router.patch("/agents/{agent_id}/network-config", response_model=AgentNetworkConfigOut)
async def update_agent_network_config(
    agent_id: str,
    update: AgentNetworkConfigUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentNetworkConfigOut:
    """Update the network configuration for an agent.

    Requires admin access. Optionally applies the MTU change immediately
    if the agent is online.
    """

    require_admin(current_user)

    # Get the agent
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    # Get or create network config
    config = (
        database.query(models.AgentNetworkConfig)
        .filter(models.AgentNetworkConfig.host_id == agent_id)
        .first()
    )

    if not config:
        import uuid
        config = models.AgentNetworkConfig(
            id=str(uuid.uuid4()),
            host_id=agent_id,
        )
        database.add(config)

    # Apply updates
    if update.data_plane_interface is not None:
        config.data_plane_interface = update.data_plane_interface
    if update.desired_mtu is not None:
        config.desired_mtu = update.desired_mtu
    # Transport config fields
    if update.transport_mode is not None:
        config.transport_mode = update.transport_mode
    if update.parent_interface is not None:
        config.parent_interface = update.parent_interface
    if update.vlan_id is not None:
        config.vlan_id = update.vlan_id
    if update.transport_ip is not None:
        config.transport_ip = update.transport_ip
    if update.transport_subnet is not None:
        config.transport_subnet = update.transport_subnet

    # Auto-assign IP from subnet if transport_ip not set but subnet is
    if config.transport_mode == "subinterface" and not config.transport_ip and config.transport_subnet:
        config.transport_ip = _next_available_transport_ip(database, config.transport_subnet, agent_id)

    database.commit()
    database.refresh(config)

    # If transport mode is subinterface and agent is online, provision the subinterface
    if config.transport_mode == "subinterface" and agent_client.is_agent_online(agent):
        try:
            result = await agent_client.provision_interface_on_agent(
                agent,
                action="create_subinterface",
                parent_interface=config.parent_interface,
                vlan_id=config.vlan_id,
                ip_cidr=config.transport_ip,
                mtu=config.desired_mtu,
            )
            config.last_sync_at = datetime.now(timezone.utc)
            if result.get("success"):
                config.current_mtu = result.get("mtu")
                config.sync_status = "synced"
                config.sync_error = None
                # Update host data_plane_address with the IP (strip CIDR suffix)
                if result.get("ip_address"):
                    ip_only = result["ip_address"].split("/")[0]
                    agent.data_plane_address = ip_only
                # Auto-create managed interface record
                _ensure_managed_interface(
                    database, agent_id, result.get("interface_name", ""),
                    "transport", config.parent_interface, config.vlan_id,
                    config.transport_ip, config.desired_mtu, config.current_mtu,
                )
            else:
                config.sync_status = "error"
                config.sync_error = result.get("error")
            database.commit()
            database.refresh(config)
        except Exception as e:
            logger.warning(f"Failed to provision transport on agent {agent_id}: {e}")
            config.sync_status = "error"
            config.sync_error = str(e)
            database.commit()
            database.refresh(config)

    elif config.transport_mode == "dedicated" and agent_client.is_agent_online(agent):
        # For dedicated mode, just configure MTU on the existing interface
        if config.data_plane_interface:
            try:
                result = await agent_client.provision_interface_on_agent(
                    agent,
                    action="configure",
                    name=config.data_plane_interface,
                    mtu=config.desired_mtu,
                    ip_cidr=config.transport_ip,
                )
                config.last_sync_at = datetime.now(timezone.utc)
                if result.get("success"):
                    config.current_mtu = result.get("mtu")
                    config.sync_status = "synced"
                    config.sync_error = None
                    if result.get("ip_address"):
                        ip_only = result["ip_address"].split("/")[0]
                        agent.data_plane_address = ip_only
                else:
                    config.sync_status = "error"
                    config.sync_error = result.get("error")
                database.commit()
                database.refresh(config)
            except Exception as e:
                logger.warning(f"Failed to configure dedicated interface on agent {agent_id}: {e}")
                config.sync_status = "error"
                config.sync_error = str(e)
                database.commit()
                database.refresh(config)

    elif config.transport_mode == "management":
        # Management mode: clear data_plane_address, use default
        agent.data_plane_address = None
        # Still sync MTU if interface is configured (legacy behavior)
        if config.data_plane_interface and agent_client.is_agent_online(agent):
            try:
                result = await agent_client.set_agent_interface_mtu(
                    agent, config.data_plane_interface, config.desired_mtu, persist=True
                )
                config.last_sync_at = datetime.now(timezone.utc)
                if result.get("success"):
                    config.current_mtu = result.get("new_mtu")
                    config.sync_status = "synced"
                    config.sync_error = None
                else:
                    config.sync_status = "error"
                    config.sync_error = result.get("error")
                database.commit()
                database.refresh(config)
            except Exception as e:
                logger.warning(f"Failed to sync MTU to agent {agent_id}: {e}")
                config.sync_status = "error"
                config.sync_error = str(e)
                database.commit()
                database.refresh(config)

    return AgentNetworkConfigOut(
        id=config.id,
        host_id=config.host_id,
        host_name=agent.name,
        data_plane_interface=config.data_plane_interface,
        desired_mtu=config.desired_mtu,
        current_mtu=config.current_mtu,
        last_sync_at=config.last_sync_at,
        sync_status=config.sync_status,
        sync_error=config.sync_error,
        transport_mode=config.transport_mode,
        parent_interface=config.parent_interface,
        vlan_id=config.vlan_id,
        transport_ip=config.transport_ip,
        transport_subnet=config.transport_subnet,
    )


@router.get("/network-configs", response_model=list[AgentNetworkConfigOut])
def list_agent_network_configs(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[AgentNetworkConfigOut]:
    """List network configurations for all agents.

    Returns the configured data plane interface and desired MTU for all
    agents, along with sync status information.
    """
    # Get all agents
    agents = database.query(models.Host).all()
    {a.id: a for a in agents}

    # Get all network configs
    configs = database.query(models.AgentNetworkConfig).all()
    config_map = {c.host_id: c for c in configs}

    result = []
    for agent in agents:
        config = config_map.get(agent.id)
        if config:
            result.append(AgentNetworkConfigOut(
                id=config.id,
                host_id=config.host_id,
                host_name=agent.name,
                data_plane_interface=config.data_plane_interface,
                desired_mtu=config.desired_mtu,
                current_mtu=config.current_mtu,
                last_sync_at=config.last_sync_at,
                sync_status=config.sync_status,
                sync_error=config.sync_error,
                transport_mode=config.transport_mode,
                parent_interface=config.parent_interface,
                vlan_id=config.vlan_id,
                transport_ip=config.transport_ip,
                transport_subnet=config.transport_subnet,
            ))
        else:
            # Return a placeholder for agents without config
            result.append(AgentNetworkConfigOut(
                id="",
                host_id=agent.id,
                host_name=agent.name,
                data_plane_interface=None,
                desired_mtu=9000,
                current_mtu=None,
                last_sync_at=None,
                sync_status="unconfigured",
                sync_error=None,
            ))

    return result


# --- Transport Config Endpoints ---


@router.get("/agents/{agent_id}/transport-config")
def get_transport_config(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Get transport configuration for agent bootstrap.

    Called by agents during two-phase bootstrap to fetch their
    transport configuration from the controller.
    Requires admin access.
    """
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    config = (
        database.query(models.AgentNetworkConfig)
        .filter(models.AgentNetworkConfig.host_id == agent_id)
        .first()
    )

    if not config or config.transport_mode == "management":
        return {
            "transport_mode": "management",
        }

    return {
        "transport_mode": config.transport_mode,
        "parent_interface": config.parent_interface,
        "vlan_id": config.vlan_id,
        "transport_ip": config.transport_ip,
        "desired_mtu": config.desired_mtu,
        "data_plane_interface": config.data_plane_interface,
    }


@router.post("/agents/{agent_id}/transport/apply")
async def apply_transport_config(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Re-apply transport config to an agent (manual recovery).

    Requires admin access. Useful when an agent's transport config
    needs to be re-provisioned after manual intervention.
    """

    require_admin(current_user)

    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    if not agent_client.is_agent_online(agent):
        raise HTTPException(status_code=503, detail="Agent is offline")

    config = (
        database.query(models.AgentNetworkConfig)
        .filter(models.AgentNetworkConfig.host_id == agent_id)
        .first()
    )

    if not config or config.transport_mode == "management":
        return {"success": True, "message": "No transport config to apply (management mode)"}

    if config.transport_mode == "subinterface":
        result = await agent_client.provision_interface_on_agent(
            agent,
            action="create_subinterface",
            parent_interface=config.parent_interface,
            vlan_id=config.vlan_id,
            ip_cidr=config.transport_ip,
            mtu=config.desired_mtu,
        )
    elif config.transport_mode == "dedicated":
        result = await agent_client.provision_interface_on_agent(
            agent,
            action="configure",
            name=config.data_plane_interface,
            mtu=config.desired_mtu,
            ip_cidr=config.transport_ip,
        )
    else:
        return {"success": False, "error": f"Unknown transport mode: {config.transport_mode}"}

    if result.get("success"):
        config.sync_status = "synced"
        config.sync_error = None
        config.current_mtu = result.get("mtu")
        config.last_sync_at = datetime.now(timezone.utc)
        if result.get("ip_address"):
            agent.data_plane_address = result["ip_address"].split("/")[0]
        database.commit()

    return result


# --- Managed Interface CRUD ---


@router.get("/interfaces", response_model=AgentManagedInterfacesResponse)
def list_managed_interfaces(
    host_id: str | None = None,
    interface_type: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentManagedInterfacesResponse:
    """List all managed interfaces, optionally filtered by host or type."""
    query = database.query(models.AgentManagedInterface)
    if host_id:
        query = query.filter(models.AgentManagedInterface.host_id == host_id)
    if interface_type:
        query = query.filter(models.AgentManagedInterface.interface_type == interface_type)

    interfaces = query.all()

    # Build host name lookup
    host_ids = {i.host_id for i in interfaces}
    hosts = database.query(models.Host).filter(models.Host.id.in_(host_ids)).all() if host_ids else []
    host_names = {h.id: h.name for h in hosts}

    result = []
    for iface in interfaces:
        out = AgentManagedInterfaceOut.model_validate(iface)
        out.host_name = host_names.get(iface.host_id)
        result.append(out)

    return AgentManagedInterfacesResponse(interfaces=result, total=len(result))


@router.get("/agents/{agent_id}/managed-interfaces", response_model=AgentManagedInterfacesResponse)
def list_agent_managed_interfaces(
    agent_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentManagedInterfacesResponse:
    """List managed interfaces for a specific agent."""
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    interfaces = (
        database.query(models.AgentManagedInterface)
        .filter(models.AgentManagedInterface.host_id == agent_id)
        .all()
    )

    result = []
    for iface in interfaces:
        out = AgentManagedInterfaceOut.model_validate(iface)
        out.host_name = agent.name
        result.append(out)

    return AgentManagedInterfacesResponse(interfaces=result, total=len(result))


@router.post("/agents/{agent_id}/managed-interfaces", response_model=AgentManagedInterfaceOut)
async def create_managed_interface(
    agent_id: str,
    request: AgentManagedInterfaceCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentManagedInterfaceOut:
    """Create and provision a managed interface on an agent host."""

    require_admin(current_user)

    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    # Determine interface name
    iface_name = request.name
    if not iface_name and request.parent_interface and request.vlan_id:
        iface_name = f"{request.parent_interface}.{request.vlan_id}"
    if not iface_name:
        raise HTTPException(status_code=400, detail="Interface name required (or provide parent_interface + vlan_id)")

    # Check for duplicates
    existing = (
        database.query(models.AgentManagedInterface)
        .filter(
            models.AgentManagedInterface.host_id == agent_id,
            models.AgentManagedInterface.name == iface_name,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Interface {iface_name} already managed on this host")

    # Provision on agent if online
    sync_status = "unconfigured"
    current_mtu = None
    actual_ip = request.ip_address
    is_up = False

    if agent_client.is_agent_online(agent):
        if request.parent_interface and request.vlan_id:
            result = await agent_client.provision_interface_on_agent(
                agent,
                action="create_subinterface",
                parent_interface=request.parent_interface,
                vlan_id=request.vlan_id,
                ip_cidr=request.ip_address,
                mtu=request.desired_mtu,
                attach_to_ovs=request.attach_to_ovs,
                ovs_vlan_tag=request.ovs_vlan_tag,
            )
        else:
            result = await agent_client.provision_interface_on_agent(
                agent,
                action="configure",
                name=iface_name,
                ip_cidr=request.ip_address,
                mtu=request.desired_mtu,
            )

        if result.get("success"):
            sync_status = "synced"
            current_mtu = result.get("mtu")
            actual_ip = result.get("ip_address") or request.ip_address
            is_up = True
        else:
            sync_status = "error"

    iface = _ensure_managed_interface(
        database, agent_id, iface_name, request.interface_type,
        request.parent_interface, request.vlan_id, actual_ip,
        request.desired_mtu, current_mtu,
    )
    iface.is_up = is_up
    iface.sync_status = sync_status
    if sync_status == "error":
        iface.sync_error = result.get("error", "Unknown error") if agent_client.is_agent_online(agent) else "Agent offline"

    # Set host data_plane_address when a transport interface is successfully provisioned
    if request.interface_type == "transport" and sync_status == "synced" and actual_ip:
        agent.data_plane_address = actual_ip.split("/")[0]

    database.commit()
    database.refresh(iface)

    out = AgentManagedInterfaceOut.model_validate(iface)
    out.host_name = agent.name
    return out


@router.patch("/interfaces/{interface_id}", response_model=AgentManagedInterfaceOut)
async def update_managed_interface(
    interface_id: str,
    update: AgentManagedInterfaceUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentManagedInterfaceOut:
    """Update a managed interface (MTU, IP)."""

    require_admin(current_user)

    iface = database.get(models.AgentManagedInterface, interface_id)
    if not iface:
        raise_not_found("Interface not found")

    agent = database.get(models.Host, iface.host_id)
    if not agent:
        raise_not_found("Agent not found")

    if update.desired_mtu is not None:
        iface.desired_mtu = update.desired_mtu
    if update.ip_address is not None:
        iface.ip_address = update.ip_address

    # Apply to agent if online
    if agent_client.is_agent_online(agent):
        result = await agent_client.provision_interface_on_agent(
            agent,
            action="configure",
            name=iface.name,
            mtu=iface.desired_mtu,
            ip_cidr=iface.ip_address,
        )
        iface.last_sync_at = datetime.now(timezone.utc)
        if result.get("success"):
            iface.current_mtu = result.get("mtu")
            iface.sync_status = "synced"
            iface.sync_error = None
        else:
            iface.sync_status = "error"
            iface.sync_error = result.get("error")

    database.commit()
    database.refresh(iface)

    out = AgentManagedInterfaceOut.model_validate(iface)
    out.host_name = agent.name
    return out


@router.delete("/interfaces/{interface_id}")
async def delete_managed_interface(
    interface_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Delete a managed interface from the agent and remove the record."""

    require_admin(current_user)

    iface = database.get(models.AgentManagedInterface, interface_id)
    if not iface:
        raise_not_found("Interface not found")

    # Check for referencing external network nodes
    ref_count = (
        database.query(models.Node)
        .filter(models.Node.managed_interface_id == interface_id)
        .count()
    )
    if ref_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: {ref_count} external network node(s) reference this interface",
        )

    agent = database.get(models.Host, iface.host_id)

    # Delete from agent if online
    if agent and agent_client.is_agent_online(agent):
        result = await agent_client.provision_interface_on_agent(
            agent, action="delete", name=iface.name,
        )
        if not result.get("success"):
            logger.warning(f"Failed to delete interface {iface.name} from agent: {result.get('error')}")

    # Clear data_plane_address if this was the transport interface providing it
    if iface.interface_type == "transport" and agent:
        remaining = (
            database.query(models.AgentManagedInterface)
            .filter(
                models.AgentManagedInterface.host_id == iface.host_id,
                models.AgentManagedInterface.interface_type == "transport",
                models.AgentManagedInterface.id != iface.id,
                models.AgentManagedInterface.sync_status == "synced",
                models.AgentManagedInterface.ip_address.isnot(None),
            )
            .first()
        )
        if remaining:
            agent.data_plane_address = remaining.ip_address.split("/")[0]
        else:
            agent.data_plane_address = None

    database.delete(iface)
    database.commit()

    return {"success": True, "message": f"Interface {iface.name} deleted"}


# --- Helper Functions ---


def _next_available_transport_ip(database: Session, subnet: str, exclude_host_id: str) -> str | None:
    """Auto-assign next available IP from a transport subnet.

    Scans existing transport_ip assignments in the same subnet and
    returns the next available host address.
    """
    import ipaddress

    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return None

    # Get all existing transport IPs in this subnet
    configs = (
        database.query(models.AgentNetworkConfig)
        .filter(
            models.AgentNetworkConfig.transport_subnet == subnet,
            models.AgentNetworkConfig.transport_ip.isnot(None),
            models.AgentNetworkConfig.host_id != exclude_host_id,
        )
        .all()
    )

    used_ips = set()
    for c in configs:
        if c.transport_ip:
            try:
                used_ips.add(ipaddress.ip_address(c.transport_ip.split("/")[0]))
            except ValueError:
                continue

    # Find next available host address (skip network and broadcast)
    prefix_len = network.prefixlen
    for addr in network.hosts():
        if addr not in used_ips:
            return f"{addr}/{prefix_len}"

    return None


def _ensure_managed_interface(
    database: Session,
    host_id: str,
    name: str,
    interface_type: str,
    parent_interface: str | None,
    vlan_id: int | None,
    ip_address: str | None,
    desired_mtu: int,
    current_mtu: int | None,
) -> models.AgentManagedInterface:
    """Create or update a managed interface record."""
    import uuid

    existing = (
        database.query(models.AgentManagedInterface)
        .filter(
            models.AgentManagedInterface.host_id == host_id,
            models.AgentManagedInterface.name == name,
        )
        .first()
    )

    if existing:
        existing.interface_type = interface_type
        existing.parent_interface = parent_interface
        existing.vlan_id = vlan_id
        existing.ip_address = ip_address
        existing.desired_mtu = desired_mtu
        existing.current_mtu = current_mtu
        existing.is_up = True
        existing.sync_status = "synced"
        existing.sync_error = None
        existing.last_sync_at = datetime.now(timezone.utc)
        database.commit()
        return existing

    iface = models.AgentManagedInterface(
        id=str(uuid.uuid4()),
        host_id=host_id,
        name=name,
        interface_type=interface_type,
        parent_interface=parent_interface,
        vlan_id=vlan_id,
        ip_address=ip_address,
        desired_mtu=desired_mtu,
        current_mtu=current_mtu,
        is_up=True,
        sync_status="synced",
        last_sync_at=datetime.now(timezone.utc),
    )
    database.add(iface)
    database.commit()
    return iface
