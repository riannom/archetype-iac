"""Infrastructure settings and agent mesh endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_admin, get_current_user
from app.utils.http import require_admin, raise_not_found
from app.schemas import (
    InfraSettingsOut,
    InfraSettingsUpdate,
    AgentLinkOut,
    AgentMeshNode,
    AgentMeshResponse,
    MtuTestRequest,
    MtuTestResponse,
    MtuTestAllResponse,
    InterfaceDetailsResponseOut,
    SetMtuRequestIn,
    SetMtuResponseOut,
    AgentNetworkConfigOut,
    AgentNetworkConfigUpdate,
    AgentManagedInterfaceOut,
    AgentManagedInterfaceCreate,
    AgentManagedInterfaceUpdate,
    AgentManagedInterfacesResponse,
    HostNicGroupOut,
    HostNicGroupCreate,
    HostNicGroupMemberOut,
    HostNicGroupMemberCreate,
    HostNicGroupsResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])


def get_or_create_settings(database: Session) -> models.InfraSettings:
    """Get the global settings row, creating it if it doesn't exist."""
    settings = database.get(models.InfraSettings, "global")
    if not settings:
        settings = models.InfraSettings(id="global")
        database.add(settings)
        database.commit()
        database.refresh(settings)
    return settings


@router.get("/settings", response_model=InfraSettingsOut)
def get_infrastructure_settings(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> InfraSettingsOut:
    """Get global infrastructure settings.

    Returns the current overlay MTU and MTU verification configuration.
    """
    settings = get_or_create_settings(database)
    return InfraSettingsOut.model_validate(settings)


@router.patch("/settings", response_model=InfraSettingsOut)
def update_infrastructure_settings(
    update: InfraSettingsUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> InfraSettingsOut:
    """Update global infrastructure settings.

    Requires admin access. Changes to overlay_mtu will affect new VXLAN
    tunnels but won't modify existing ones.
    """

    settings = get_or_create_settings(database)

    # Apply updates
    if update.overlay_mtu is not None:
        settings.overlay_mtu = update.overlay_mtu
    if update.mtu_verification_enabled is not None:
        settings.mtu_verification_enabled = update.mtu_verification_enabled
    if update.overlay_preserve_container_mtu is not None:
        settings.overlay_preserve_container_mtu = update.overlay_preserve_container_mtu
    if update.overlay_clamp_host_mtu is not None:
        settings.overlay_clamp_host_mtu = update.overlay_clamp_host_mtu
    if update.login_dark_theme_id is not None:
        settings.login_dark_theme_id = update.login_dark_theme_id
    if update.login_dark_background_id is not None:
        settings.login_dark_background_id = update.login_dark_background_id
    if update.login_dark_background_opacity is not None:
        settings.login_dark_background_opacity = update.login_dark_background_opacity
    if update.login_light_theme_id is not None:
        settings.login_light_theme_id = update.login_light_theme_id
    if update.login_light_background_id is not None:
        settings.login_light_background_id = update.login_light_background_id
    if update.login_light_background_opacity is not None:
        settings.login_light_background_opacity = update.login_light_background_opacity

    settings.updated_by_id = current_user.id
    settings.updated_at = datetime.now(timezone.utc)

    database.commit()
    database.refresh(settings)

    logger.info(
        f"Infrastructure settings updated by {current_user.email}: "
        f"overlay_mtu={settings.overlay_mtu}, "
        f"mtu_verification_enabled={settings.mtu_verification_enabled}, "
        f"overlay_preserve_container_mtu={settings.overlay_preserve_container_mtu}, "
        f"overlay_clamp_host_mtu={settings.overlay_clamp_host_mtu}"
    )

    return InfraSettingsOut.model_validate(settings)


@router.get("/mesh", response_model=AgentMeshResponse)
def get_agent_mesh(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentMeshResponse:
    """Get the agent mesh for visualization.

    Returns all agents and their connectivity links with MTU test results.
    Links are automatically created for agent pairs on first access.
    """
    # Get all agents
    agents = database.query(models.Host).all()

    # Backfill data_plane_address from transport managed interfaces when missing
    updated = False
    for agent in agents:
        if agent.data_plane_address:
            continue
        transport_iface = (
            database.query(models.AgentManagedInterface)
            .filter(
                models.AgentManagedInterface.host_id == agent.id,
                models.AgentManagedInterface.interface_type == "transport",
                models.AgentManagedInterface.sync_status == "synced",
                models.AgentManagedInterface.ip_address.isnot(None),
            )
            .first()
        )
        if transport_iface and transport_iface.ip_address:
            agent.data_plane_address = transport_iface.ip_address.split("/")[0]
            updated = True
    if updated:
        database.commit()

    # Build agent nodes list
    agent_nodes = [
        AgentMeshNode(
            id=agent.id,
            name=agent.name,
            address=agent.address,
            status=agent.status,
        )
        for agent in agents
    ]

    # Get settings
    settings = get_or_create_settings(database)

    # Pre-load data plane addresses and network configs
    # First check hosts.data_plane_address, then fall back to transport managed interfaces
    agent_dp_map: dict[str, str | None] = {a.id: a.data_plane_address for a in agents}
    transport_ifaces = (
        database.query(models.AgentManagedInterface)
        .filter(
            models.AgentManagedInterface.interface_type == "transport",
            models.AgentManagedInterface.sync_status == "synced",
            models.AgentManagedInterface.ip_address.isnot(None),
        )
        .all()
    )
    # Also build a map of transport interface MTUs for data plane testing
    transport_mtu_map: dict[str, int] = {}
    for iface in transport_ifaces:
        if not agent_dp_map.get(iface.host_id):
            # Use transport interface IP (strip CIDR suffix)
            agent_dp_map[iface.host_id] = iface.ip_address.split("/")[0] if iface.ip_address else None
        transport_mtu_map[iface.host_id] = iface.desired_mtu
    net_configs = {
        c.host_id: c
        for c in database.query(models.AgentNetworkConfig).all()
    }

    # Get or create agent links for all pairs (both management and data_plane)
    agent_ids = [a.id for a in agents]
    links = []

    def _ensure_link(src_id: str, tgt_id: str, path: str, mtu: int) -> None:
        """Create a link record if it doesn't exist for this (src, tgt, path) triple."""
        existing = (
            database.query(models.AgentLink)
            .filter(
                models.AgentLink.source_agent_id == src_id,
                models.AgentLink.target_agent_id == tgt_id,
                models.AgentLink.test_path == path,
            )
            .first()
        )
        if not existing:
            database.add(models.AgentLink(
                source_agent_id=src_id,
                target_agent_id=tgt_id,
                configured_mtu=mtu,
                test_path=path,
            ))

    for i, source_id in enumerate(agent_ids):
        for target_id in agent_ids[i + 1:]:
            # Always create management path links (A->B and B->A)
            _ensure_link(source_id, target_id, "management", 1500)
            _ensure_link(target_id, source_id, "management", 1500)

            # Create data_plane path links if both agents have data_plane_address
            if agent_dp_map.get(source_id) and agent_dp_map.get(target_id):
                # Resolve MTU: prefer network config, fall back to transport managed interface
                src_mtu = (net_configs.get(source_id).desired_mtu if net_configs.get(source_id) and net_configs[source_id].desired_mtu > settings.overlay_mtu else None) or transport_mtu_map.get(source_id)
                tgt_mtu = (net_configs.get(target_id).desired_mtu if net_configs.get(target_id) and net_configs[target_id].desired_mtu > settings.overlay_mtu else None) or transport_mtu_map.get(target_id)
                dp_mtu = min(src_mtu, tgt_mtu) if src_mtu and tgt_mtu else (src_mtu or tgt_mtu or settings.overlay_mtu)
                _ensure_link(source_id, target_id, "data_plane", dp_mtu)
                _ensure_link(target_id, source_id, "data_plane", dp_mtu)

    database.commit()

    # Build agent name lookup
    agent_name_map = {a.id: a.name for a in agents}

    # Get all links
    all_links = database.query(models.AgentLink).all()
    for link in all_links:
        links.append(
            AgentLinkOut(
                id=link.id,
                source_agent_id=link.source_agent_id,
                source_agent_name=agent_name_map.get(link.source_agent_id),
                target_agent_id=link.target_agent_id,
                target_agent_name=agent_name_map.get(link.target_agent_id),
                link_type=link.link_type,
                configured_mtu=link.configured_mtu,
                tested_mtu=link.tested_mtu,
                last_test_at=link.last_test_at,
                test_status=link.test_status,
                test_error=link.test_error,
                latency_ms=link.latency_ms,
                test_path=link.test_path,
            )
        )

    return AgentMeshResponse(
        agents=agent_nodes,
        links=links,
        settings=InfraSettingsOut.model_validate(settings),
    )


@router.post("/mesh/test-mtu", response_model=MtuTestResponse)
async def test_mtu_between_agents(
    request: MtuTestRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> MtuTestResponse:
    """Test MTU connectivity between two agents.

    Runs a ping with DF (Don't Fragment) bit set to verify the path
    supports the configured MTU. Also detects link type (direct/routed)
    via TTL analysis.
    """
    from app import agent_client

    # Get source and target agents
    source_agent = database.get(models.Host, request.source_agent_id)
    target_agent = database.get(models.Host, request.target_agent_id)

    if not source_agent:
        raise_not_found("Source agent not found")
    if not target_agent:
        raise_not_found("Target agent not found")

    if not agent_client.is_agent_online(source_agent):
        return MtuTestResponse(
            success=False,
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=0,
            error="Source agent is offline",
        )

    if not agent_client.is_agent_online(target_agent):
        return MtuTestResponse(
            success=False,
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=0,
            error="Target agent is offline",
        )

    # Get settings
    settings = get_or_create_settings(database)

    # Resolve data plane addresses: check host field first, then transport managed interfaces
    from app.agent_client import resolve_agent_ip

    def _resolve_dp_address(agent: models.Host) -> str | None:
        if agent.data_plane_address:
            return agent.data_plane_address
        iface = (
            database.query(models.AgentManagedInterface)
            .filter(
                models.AgentManagedInterface.host_id == agent.id,
                models.AgentManagedInterface.interface_type == "transport",
                models.AgentManagedInterface.sync_status == "synced",
                models.AgentManagedInterface.ip_address.isnot(None),
            )
            .first()
        )
        if iface and iface.ip_address:
            return iface.ip_address.split("/")[0]
        return None

    source_dp = _resolve_dp_address(source_agent)
    target_dp = _resolve_dp_address(target_agent)
    source_ip: str | None = None

    # Determine test path: use explicit path if provided, otherwise auto-detect
    if request.test_path is not None:
        # Explicit path requested
        test_path = request.test_path
    elif source_dp and target_dp:
        # Auto-detect: both have data plane addresses — test on data plane path
        test_path = "data_plane"
    else:
        # Auto-detect: fall back to management
        test_path = "management"

    if test_path == "data_plane":
        if not source_dp or not target_dp:
            return MtuTestResponse(
                success=False,
                source_agent_id=request.source_agent_id,
                target_agent_id=request.target_agent_id,
                configured_mtu=0,
                test_path=test_path,
                error="Both agents must have data plane addresses for data plane testing",
            )
        target_ip = target_dp
        source_ip = source_dp
    else:
        target_ip = await resolve_agent_ip(target_agent.address)

    # Determine test MTU: use transport MTU on data plane, 1500 on management
    test_mtu = 1500 if test_path == "management" else settings.overlay_mtu
    if test_path == "data_plane":
        def _resolve_dp_mtu(host_id: str) -> int | None:
            """Get data plane MTU: prefer network config if > overlay, else transport managed interface."""
            cfg = (
                database.query(models.AgentNetworkConfig)
                .filter(models.AgentNetworkConfig.host_id == host_id)
                .first()
            )
            if cfg and cfg.desired_mtu and cfg.desired_mtu > settings.overlay_mtu:
                return cfg.desired_mtu
            iface = (
                database.query(models.AgentManagedInterface)
                .filter(
                    models.AgentManagedInterface.host_id == host_id,
                    models.AgentManagedInterface.interface_type == "transport",
                    models.AgentManagedInterface.sync_status == "synced",
                )
                .first()
            )
            if iface:
                return iface.desired_mtu
            return cfg.desired_mtu if cfg and cfg.desired_mtu else None

        src_mtu = _resolve_dp_mtu(request.source_agent_id)
        tgt_mtu = _resolve_dp_mtu(request.target_agent_id)
        if src_mtu and tgt_mtu:
            test_mtu = min(src_mtu, tgt_mtu)

    # Get or create the link record (filtered by test_path)
    link = (
        database.query(models.AgentLink)
        .filter(
            models.AgentLink.source_agent_id == request.source_agent_id,
            models.AgentLink.target_agent_id == request.target_agent_id,
            models.AgentLink.test_path == test_path,
        )
        .first()
    )
    if not link:
        link = models.AgentLink(
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=test_mtu,
            test_path=test_path,
        )
        database.add(link)
        database.commit()
        database.refresh(link)

    # Update link to pending status
    link.test_status = "pending"
    link.configured_mtu = test_mtu
    link.test_path = test_path
    database.commit()

    try:
        # Call agent to perform MTU test
        result = await agent_client.test_mtu_on_agent(
            source_agent,
            target_ip,
            test_mtu,
            source_ip=source_ip,
        )

        # Update link record with results
        link.last_test_at = datetime.now(timezone.utc)
        if result.get("success"):
            link.test_status = "success"
            link.tested_mtu = result.get("tested_mtu", test_mtu)
            link.link_type = result.get("link_type", "unknown")
            link.latency_ms = result.get("latency_ms")
            link.test_error = None
        else:
            link.test_status = "failed"
            link.test_error = result.get("error", "Unknown error")
            link.tested_mtu = None

        database.commit()

        return MtuTestResponse(
            success=result.get("success", False),
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=test_mtu,
            tested_mtu=result.get("tested_mtu"),
            link_type=result.get("link_type"),
            latency_ms=result.get("latency_ms"),
            test_path=test_path,
            error=result.get("error"),
        )

    except Exception as e:
        logger.error(f"MTU test failed: {e}")
        link.test_status = "failed"
        link.test_error = str(e)
        link.last_test_at = datetime.now(timezone.utc)
        database.commit()

        return MtuTestResponse(
            success=False,
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=test_mtu,
            error=str(e),
        )


@router.post("/mesh/test-all", response_model=MtuTestAllResponse)
async def test_all_agent_pairs(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> MtuTestAllResponse:
    """Test MTU connectivity between all online agent pairs.

    Runs MTU tests between all pairs of online agents in both directions.
    Returns aggregated results.
    """
    from app import agent_client

    # Get all online agents
    agents = (
        database.query(models.Host)
        .filter(models.Host.status == "online")
        .all()
    )

    # Filter to actually online agents
    online_agents = [a for a in agents if agent_client.is_agent_online(a)]

    if len(online_agents) < 2:
        return MtuTestAllResponse(
            total_pairs=0,
            successful=0,
            failed=0,
            results=[],
        )

    results = []
    successful = 0
    failed = 0

    # Build set of agent IDs that have data plane addresses (host field or transport interfaces)
    dp_agent_ids: set[str] = set()
    for a in online_agents:
        if a.data_plane_address:
            dp_agent_ids.add(a.id)
    transport_ifaces = (
        database.query(models.AgentManagedInterface.host_id)
        .filter(
            models.AgentManagedInterface.interface_type == "transport",
            models.AgentManagedInterface.sync_status == "synced",
            models.AgentManagedInterface.ip_address.isnot(None),
        )
        .distinct()
        .all()
    )
    for (host_id,) in transport_ifaces:
        dp_agent_ids.add(host_id)

    # Test all pairs in both directions, testing both paths when applicable
    for i, source_agent in enumerate(online_agents):
        for target_agent in online_agents[i + 1:]:
            # Determine which paths to test for this pair
            paths_to_test = ["management"]
            if source_agent.id in dp_agent_ids and target_agent.id in dp_agent_ids:
                paths_to_test.append("data_plane")

            for path in paths_to_test:
                # Test A -> B
                request_ab = MtuTestRequest(
                    source_agent_id=source_agent.id,
                    target_agent_id=target_agent.id,
                    test_path=path,
                )
                result_ab = await test_mtu_between_agents(request_ab, database, current_user)
                results.append(result_ab)
                if result_ab.success:
                    successful += 1
                else:
                    failed += 1

                # Test B -> A
                request_ba = MtuTestRequest(
                    source_agent_id=target_agent.id,
                    target_agent_id=source_agent.id,
                    test_path=path,
                )
                result_ba = await test_mtu_between_agents(request_ba, database, current_user)
                results.append(result_ba)
                if result_ba.success:
                    successful += 1
                else:
                    failed += 1

    return MtuTestAllResponse(
        total_pairs=len(results),
        successful=successful,
        failed=failed,
        results=results,
    )


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
    from app import agent_client

    # Get the agent
    agent = database.get(models.Host, agent_id)
    if not agent:
        raise_not_found("Agent not found")

    if not agent_client.is_agent_online(agent):
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        result = await agent_client.get_agent_interface_details(agent)
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
    from app import agent_client

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
    from app import agent_client

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
    from app import agent_client

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
    from app import agent_client

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
    from app import agent_client

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
    from app import agent_client

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


# --- NIC Group CRUD (future interface affinity) ---


@router.get("/nic-groups", response_model=HostNicGroupsResponse)
def list_nic_groups(
    host_id: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> HostNicGroupsResponse:
    """List NIC groups, optionally filtered by host."""
    query = database.query(models.HostNicGroup)
    if host_id:
        query = query.filter(models.HostNicGroup.host_id == host_id)

    groups = query.all()

    host_ids = {g.host_id for g in groups}
    hosts = database.query(models.Host).filter(models.Host.id.in_(host_ids)).all() if host_ids else []
    host_names = {h.id: h.name for h in hosts}

    group_ids = {g.id for g in groups}
    members = (
        database.query(models.HostNicGroupMember)
        .filter(models.HostNicGroupMember.nic_group_id.in_(group_ids))
        .all()
        if group_ids else []
    )
    interface_ids = {m.managed_interface_id for m in members}
    interfaces = (
        database.query(models.AgentManagedInterface)
        .filter(models.AgentManagedInterface.id.in_(interface_ids))
        .all()
        if interface_ids else []
    )
    interface_lookup = {iface.id: iface for iface in interfaces}

    members_by_group: dict[str, list[HostNicGroupMemberOut]] = {}
    for member in members:
        out = HostNicGroupMemberOut.model_validate(member)
        iface = interface_lookup.get(member.managed_interface_id)
        if iface:
            out.interface_name = iface.name
            out.interface_type = iface.interface_type
        members_by_group.setdefault(member.nic_group_id, []).append(out)

    result = []
    for group in groups:
        out = HostNicGroupOut.model_validate(group)
        out.host_name = host_names.get(group.host_id)
        out.members = members_by_group.get(group.id, [])
        result.append(out)

    return HostNicGroupsResponse(groups=result, total=len(result))


@router.post("/hosts/{host_id}/nic-groups", response_model=HostNicGroupOut)
def create_nic_group(
    host_id: str,
    request: HostNicGroupCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> HostNicGroupOut:
    """Create a NIC group on a host."""

    host = database.get(models.Host, host_id)
    if not host:
        raise_not_found("Host not found")

    existing = (
        database.query(models.HostNicGroup)
        .filter(models.HostNicGroup.host_id == host_id, models.HostNicGroup.name == request.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"NIC group {request.name} already exists on this host")

    group = models.HostNicGroup(
        host_id=host_id,
        name=request.name,
        description=request.description,
    )
    database.add(group)
    database.commit()
    database.refresh(group)

    out = HostNicGroupOut.model_validate(group)
    out.host_name = host.name
    out.members = []
    return out


@router.post("/nic-groups/{group_id}/members", response_model=HostNicGroupMemberOut)
def add_nic_group_member(
    group_id: str,
    request: HostNicGroupMemberCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> HostNicGroupMemberOut:
    """Add a managed interface to a NIC group."""

    group = database.get(models.HostNicGroup, group_id)
    if not group:
        raise_not_found("NIC group not found")

    iface = database.get(models.AgentManagedInterface, request.managed_interface_id)
    if not iface:
        raise_not_found("Managed interface not found")

    if iface.host_id != group.host_id:
        raise HTTPException(status_code=400, detail="Managed interface belongs to a different host")

    existing = (
        database.query(models.HostNicGroupMember)
        .filter(
            models.HostNicGroupMember.nic_group_id == group_id,
            models.HostNicGroupMember.managed_interface_id == request.managed_interface_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Interface already in this NIC group")

    member = models.HostNicGroupMember(
        nic_group_id=group_id,
        managed_interface_id=request.managed_interface_id,
        role=request.role,
    )
    database.add(member)
    database.commit()
    database.refresh(member)

    out = HostNicGroupMemberOut.model_validate(member)
    out.interface_name = iface.name
    out.interface_type = iface.interface_type
    return out


@router.delete("/nic-groups/{group_id}/members/{member_id}")
def delete_nic_group_member(
    group_id: str,
    member_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Remove a member from a NIC group."""

    member = database.get(models.HostNicGroupMember, member_id)
    if not member or member.nic_group_id != group_id:
        raise_not_found("NIC group member not found")

    database.delete(member)
    database.commit()

    return {"success": True}


@router.delete("/nic-groups/{group_id}")
def delete_nic_group(
    group_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Delete a NIC group and its members."""

    group = database.get(models.HostNicGroup, group_id)
    if not group:
        raise_not_found("NIC group not found")

    database.delete(group)
    database.commit()

    return {"success": True}


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
