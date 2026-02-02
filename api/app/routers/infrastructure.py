"""Infrastructure settings and agent mesh endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_user
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
async def get_infrastructure_settings(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> InfraSettingsOut:
    """Get global infrastructure settings.

    Returns the current overlay MTU and MTU verification configuration.
    """
    settings = get_or_create_settings(database)
    return InfraSettingsOut.model_validate(settings)


@router.patch("/settings", response_model=InfraSettingsOut)
async def update_infrastructure_settings(
    update: InfraSettingsUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> InfraSettingsOut:
    """Update global infrastructure settings.

    Requires admin access. Changes to overlay_mtu will affect new VXLAN
    tunnels but won't modify existing ones.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    settings = get_or_create_settings(database)

    # Apply updates
    if update.overlay_mtu is not None:
        settings.overlay_mtu = update.overlay_mtu
    if update.mtu_verification_enabled is not None:
        settings.mtu_verification_enabled = update.mtu_verification_enabled

    settings.updated_by_id = current_user.id
    settings.updated_at = datetime.now(timezone.utc)

    database.commit()
    database.refresh(settings)

    logger.info(
        f"Infrastructure settings updated by {current_user.email}: "
        f"overlay_mtu={settings.overlay_mtu}, "
        f"mtu_verification_enabled={settings.mtu_verification_enabled}"
    )

    return InfraSettingsOut.model_validate(settings)


@router.get("/mesh", response_model=AgentMeshResponse)
async def get_agent_mesh(
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentMeshResponse:
    """Get the agent mesh for visualization.

    Returns all agents and their connectivity links with MTU test results.
    Links are automatically created for agent pairs on first access.
    """
    # Get all agents
    agents = database.query(models.Host).all()

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

    # Get or create agent links for all pairs
    agent_ids = [a.id for a in agents]
    links = []

    # Create missing agent link records
    for i, source_id in enumerate(agent_ids):
        for target_id in agent_ids[i + 1:]:
            # Check for existing link A->B
            link_ab = (
                database.query(models.AgentLink)
                .filter(
                    models.AgentLink.source_agent_id == source_id,
                    models.AgentLink.target_agent_id == target_id,
                )
                .first()
            )
            if not link_ab:
                link_ab = models.AgentLink(
                    source_agent_id=source_id,
                    target_agent_id=target_id,
                    configured_mtu=settings.overlay_mtu,
                )
                database.add(link_ab)

            # Check for existing link B->A
            link_ba = (
                database.query(models.AgentLink)
                .filter(
                    models.AgentLink.source_agent_id == target_id,
                    models.AgentLink.target_agent_id == source_id,
                )
                .first()
            )
            if not link_ba:
                link_ba = models.AgentLink(
                    source_agent_id=target_id,
                    target_agent_id=source_id,
                    configured_mtu=settings.overlay_mtu,
                )
                database.add(link_ba)

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
        raise HTTPException(status_code=404, detail="Source agent not found")
    if not target_agent:
        raise HTTPException(status_code=404, detail="Target agent not found")

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

    # Extract target IP from agent address (host:port format)
    target_ip = target_agent.address.split(":")[0]

    # Get or create the link record
    link = (
        database.query(models.AgentLink)
        .filter(
            models.AgentLink.source_agent_id == request.source_agent_id,
            models.AgentLink.target_agent_id == request.target_agent_id,
        )
        .first()
    )
    if not link:
        link = models.AgentLink(
            source_agent_id=request.source_agent_id,
            target_agent_id=request.target_agent_id,
            configured_mtu=settings.overlay_mtu,
        )
        database.add(link)
        database.commit()
        database.refresh(link)

    # Update link to pending status
    link.test_status = "pending"
    link.configured_mtu = settings.overlay_mtu
    database.commit()

    try:
        # Call agent to perform MTU test
        result = await agent_client.test_mtu_on_agent(
            source_agent,
            target_ip,
            settings.overlay_mtu,
        )

        # Update link record with results
        link.last_test_at = datetime.now(timezone.utc)
        if result.get("success"):
            link.test_status = "success"
            link.tested_mtu = result.get("tested_mtu", settings.overlay_mtu)
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
            configured_mtu=settings.overlay_mtu,
            tested_mtu=result.get("tested_mtu"),
            link_type=result.get("link_type"),
            latency_ms=result.get("latency_ms"),
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
            configured_mtu=settings.overlay_mtu,
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

    settings = get_or_create_settings(database)
    results = []
    successful = 0
    failed = 0

    # Test all pairs in both directions
    for i, source_agent in enumerate(online_agents):
        for target_agent in online_agents[i + 1:]:
            # Test A -> B
            request_ab = MtuTestRequest(
                source_agent_id=source_agent.id,
                target_agent_id=target_agent.id,
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
