"""Agent registration and management endpoints."""

from __future__ import annotations

import json
from datetime import datetime

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


# --- Endpoints ---

@router.post("/register", response_model=RegistrationResponse)
def register_agent(
    request: RegistrationRequest,
    database: Session = Depends(db.get_db),
) -> RegistrationResponse:
    """Register a new agent or update existing registration."""
    agent = request.agent

    # Check if agent already exists
    existing = database.get(models.Host, agent.agent_id)

    if existing:
        # Update existing registration
        existing.name = agent.name
        existing.address = agent.address
        existing.status = "online"
        existing.capabilities = json.dumps(agent.capabilities.model_dump())
        existing.version = agent.version
        existing.last_heartbeat = datetime.utcnow()
        database.commit()

        return RegistrationResponse(
            success=True,
            message="Agent re-registered",
            assigned_id=agent.agent_id,
        )

    # Create new agent
    host = models.Host(
        id=agent.agent_id,
        name=agent.name,
        address=agent.address,
        status="online",
        capabilities=json.dumps(agent.capabilities.model_dump()),
        version=agent.version,
        last_heartbeat=datetime.utcnow(),
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

    # Update status
    host.status = request.status
    host.last_heartbeat = datetime.utcnow()
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
