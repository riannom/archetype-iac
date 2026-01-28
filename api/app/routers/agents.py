"""Agent registration and management endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import db, models


router = APIRouter(prefix="/agents", tags=["agents"])


# --- Request/Response Schemas ---

class AgentCapabilities(BaseModel):
    """What the agent can do."""
    providers: list[str] = Field(default_factory=list)
    max_concurrent_jobs: int = 4
    features: list[str] = Field(default_factory=list)


class AgentInfo(BaseModel):
    """Agent identification and capabilities."""
    agent_id: str
    name: str
    address: str
    capabilities: AgentCapabilities
    version: str = "0.1.0"


class RegistrationRequest(BaseModel):
    """Agent -> Controller: Register this agent."""
    agent: AgentInfo
    token: str | None = None


class RegistrationResponse(BaseModel):
    """Controller -> Agent: Registration result."""
    success: bool
    message: str = ""
    assigned_id: str | None = None


class HeartbeatRequest(BaseModel):
    """Agent -> Controller: I'm still alive."""
    agent_id: str
    status: str = "online"
    active_jobs: int = 0
    resource_usage: dict = Field(default_factory=dict)


class HeartbeatResponse(BaseModel):
    """Controller -> Agent: Acknowledged."""
    acknowledged: bool
    pending_jobs: list[str] = Field(default_factory=list)


class HostOut(BaseModel):
    """Host info for API responses."""
    id: str
    name: str
    address: str
    status: str
    capabilities: dict
    version: str
    last_heartbeat: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class DashboardMetrics(BaseModel):
    """System-wide metrics for dashboard display."""
    agents: dict  # {"online": int, "total": int}
    containers: dict  # {"running": int, "total": int}
    cpu_percent: float
    memory_percent: float
    labs_running: int
    labs_total: int


# --- Endpoints ---

@router.post("/register", response_model=RegistrationResponse)
def register_agent(
    request: RegistrationRequest,
    database: Session = Depends(db.get_db),
) -> RegistrationResponse:
    """Register a new agent or update existing registration.

    Prevents duplicate agents by checking name and address.
    If an agent with the same name or address already exists,
    updates that record instead of creating a new one.
    """
    agent = request.agent

    # First check if agent already exists by ID
    existing = database.get(models.Host, agent.agent_id)

    if existing:
        # Update existing registration (same agent reconnecting)
        existing.name = agent.name
        existing.address = agent.address
        existing.status = "online"
        existing.capabilities = json.dumps(agent.capabilities.model_dump())
        existing.version = agent.version
        existing.last_heartbeat = datetime.now(timezone.utc)
        database.commit()

        return RegistrationResponse(
            success=True,
            message="Agent re-registered",
            assigned_id=agent.agent_id,
        )

    # Check for existing agent with same name or address (agent restarted with new ID)
    existing_by_name = (
        database.query(models.Host)
        .filter(models.Host.name == agent.name)
        .first()
    )
    existing_by_address = (
        database.query(models.Host)
        .filter(models.Host.address == agent.address)
        .first()
    )

    # Prefer matching by name, fall back to address
    existing_duplicate = existing_by_name or existing_by_address

    if existing_duplicate:
        # Update existing record in place to preserve foreign key references
        # (labs and jobs may reference this agent)
        existing_duplicate.name = agent.name
        existing_duplicate.address = agent.address
        existing_duplicate.status = "online"
        existing_duplicate.capabilities = json.dumps(agent.capabilities.model_dump())
        existing_duplicate.version = agent.version
        existing_duplicate.last_heartbeat = datetime.now(timezone.utc)
        database.commit()

        # Return the existing ID so agent can use it for heartbeats
        return RegistrationResponse(
            success=True,
            message="Agent re-registered (updated existing record)",
            assigned_id=existing_duplicate.id,
        )

    # Create new agent (first time registration)
    host = models.Host(
        id=agent.agent_id,
        name=agent.name,
        address=agent.address,
        status="online",
        capabilities=json.dumps(agent.capabilities.model_dump()),
        version=agent.version,
        last_heartbeat=datetime.now(timezone.utc),
    )
    database.add(host)
    database.commit()

    return RegistrationResponse(
        success=True,
        message="Agent registered",
        assigned_id=agent.agent_id,
    )


@router.post("/{agent_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    agent_id: str,
    request: HeartbeatRequest,
    database: Session = Depends(db.get_db),
) -> HeartbeatResponse:
    """Receive heartbeat from agent."""
    host = database.get(models.Host, agent_id)

    if not host:
        raise HTTPException(status_code=404, detail="Agent not registered")

    # Update status and resource usage
    host.status = request.status
    host.resource_usage = json.dumps(request.resource_usage)
    host.last_heartbeat = datetime.now(timezone.utc)
    database.commit()

    # TODO: Check for pending jobs to dispatch
    pending_jobs: list[str] = []

    return HeartbeatResponse(
        acknowledged=True,
        pending_jobs=pending_jobs,
    )


@router.get("", response_model=list[HostOut])
def list_agents(
    database: Session = Depends(db.get_db),
) -> list[HostOut]:
    """List all registered agents."""
    hosts = database.query(models.Host).order_by(models.Host.name).all()

    result = []
    for host in hosts:
        try:
            capabilities = json.loads(host.capabilities)
        except (json.JSONDecodeError, TypeError):
            capabilities = {}

        result.append(HostOut(
            id=host.id,
            name=host.name,
            address=host.address,
            status=host.status,
            capabilities=capabilities,
            version=host.version,
            last_heartbeat=host.last_heartbeat,
            created_at=host.created_at,
        ))

    return result


@router.get("/detailed")
def list_agents_detailed(
    database: Session = Depends(db.get_db),
) -> list[dict]:
    """List all agents with full details including resource usage, role, and labs.

    Role is determined by:
    - "agent": Has containerlab or libvirt provider capabilities
    - "controller": Has no provider capabilities (controller-only host)
    - "agent+controller": Has provider capabilities AND is the same host as controller
    """
    hosts = database.query(models.Host).order_by(models.Host.name).all()

    # Get labs to associate with hosts
    all_labs = database.query(models.Lab).all()
    labs_by_agent: dict[str, list[dict]] = {}
    for lab in all_labs:
        if lab.agent_id:
            if lab.agent_id not in labs_by_agent:
                labs_by_agent[lab.agent_id] = []
            labs_by_agent[lab.agent_id].append({
                "id": lab.id,
                "name": lab.name,
                "state": lab.state,
            })

    result = []
    for host in hosts:
        try:
            capabilities = json.loads(host.capabilities) if host.capabilities else {}
        except (json.JSONDecodeError, TypeError):
            capabilities = {}

        try:
            resource_usage = json.loads(host.resource_usage) if host.resource_usage else {}
        except (json.JSONDecodeError, TypeError):
            resource_usage = {}

        # Determine role based on capabilities
        providers = capabilities.get("providers", [])
        has_provider = len(providers) > 0

        # Check if this host is co-located with controller
        # (controller runs on localhost, so check if address matches common patterns)
        address = host.address.lower()
        is_local = (
            address.startswith("localhost") or
            address.startswith("127.0.0.1") or
            address.startswith("host.docker.internal")
        )

        if has_provider:
            role = "agent+controller" if is_local else "agent"
        else:
            role = "controller"

        # Get labs for this host
        host_labs = labs_by_agent.get(host.id, [])

        result.append({
            "id": host.id,
            "name": host.name,
            "address": host.address,
            "status": host.status,
            "version": host.version,
            "role": role,
            "capabilities": capabilities,
            "resource_usage": {
                "cpu_percent": resource_usage.get("cpu_percent", 0),
                "memory_percent": resource_usage.get("memory_percent", 0),
                "memory_used_gb": resource_usage.get("memory_used_gb", 0),
                "memory_total_gb": resource_usage.get("memory_total_gb", 0),
                "storage_percent": resource_usage.get("disk_percent", 0),
                "storage_used_gb": resource_usage.get("disk_used_gb", 0),
                "storage_total_gb": resource_usage.get("disk_total_gb", 0),
                "containers_running": resource_usage.get("containers_running", 0),
                "containers_total": resource_usage.get("containers_total", 0),
            },
            "labs": host_labs,
            "lab_count": len(host_labs),
            "last_heartbeat": host.last_heartbeat.isoformat() if host.last_heartbeat else None,
        })

    return result


@router.get("/{agent_id}", response_model=HostOut)
def get_agent(
    agent_id: str,
    database: Session = Depends(db.get_db),
) -> HostOut:
    """Get details of a specific agent."""
    host = database.get(models.Host, agent_id)

    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        capabilities = json.loads(host.capabilities)
    except (json.JSONDecodeError, TypeError):
        capabilities = {}

    return HostOut(
        id=host.id,
        name=host.name,
        address=host.address,
        status=host.status,
        capabilities=capabilities,
        version=host.version,
        last_heartbeat=host.last_heartbeat,
        created_at=host.created_at,
    )


@router.delete("/{agent_id}")
def unregister_agent(
    agent_id: str,
    database: Session = Depends(db.get_db),
) -> dict[str, str]:
    """Unregister an agent."""
    host = database.get(models.Host, agent_id)

    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    database.delete(host)
    database.commit()

    return {"status": "deleted"}


# --- Network Interface/Bridge Discovery Proxy ---

@router.get("/{agent_id}/interfaces")
async def list_agent_interfaces(
    agent_id: str,
    database: Session = Depends(db.get_db),
) -> dict:
    """Proxy request to agent for listing available network interfaces.

    Used for external network configuration (VLAN parent interfaces).
    """
    import httpx

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{host.address}/interfaces")
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent error: {e}")


@router.get("/{agent_id}/bridges")
async def list_agent_bridges(
    agent_id: str,
    database: Session = Depends(db.get_db),
) -> dict:
    """Proxy request to agent for listing available Linux bridges.

    Used for external network configuration (bridge mode).
    """
    import httpx

    host = database.get(models.Host, agent_id)
    if not host:
        raise HTTPException(status_code=404, detail="Agent not found")

    if host.status != "online":
        raise HTTPException(status_code=503, detail="Agent is offline")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{host.address}/bridges")
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact agent: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Agent error: {e}")
