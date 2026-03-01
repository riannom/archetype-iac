"""Infrastructure settings and agent mesh endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import agent_client, db, models
from app.auth import get_current_admin, get_current_user
from app.utils.http import raise_not_found
from app.schemas import (
    InfraSettingsOut,
    InfraSettingsUpdate,
    AgentLinkOut,
    AgentMeshNode,
    AgentMeshResponse,
    MtuTestRequest,
    MtuTestResponse,
    MtuTestAllResponse,
)

from app.routers.infrastructure_interfaces import router as interfaces_router
from app.routers.infrastructure_nic_groups import router as nic_groups_router


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/infrastructure", tags=["infrastructure"])
router.include_router(interfaces_router)
router.include_router(nic_groups_router)


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
