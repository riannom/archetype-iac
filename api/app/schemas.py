from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LabCreate(BaseModel):
    name: str


class LabOut(BaseModel):
    id: str
    name: str
    owner_id: str | None
    workspace_path: str
    state: str = "stopped"
    agent_id: str | None = None
    state_updated_at: datetime | None = None
    state_error: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class LabYamlIn(BaseModel):
    content: str


class LabYamlOut(BaseModel):
    content: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GraphEndpoint(BaseModel):
    node: str
    ifname: str | None = None


class GraphLink(BaseModel):
    endpoints: list[GraphEndpoint]
    type: str | None = None
    name: str | None = None
    pool: str | None = None
    prefix: str | None = None
    bridge: str | None = None
    mtu: int | None = None
    bandwidth: int | None = None


class GraphNode(BaseModel):
    id: str
    name: str
    device: str | None = None
    image: str | None = None
    version: str | None = None
    role: str | None = None
    mgmt: dict | None = None
    vars: dict | None = None


class TopologyGraph(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    defaults: dict | None = None


class JobOut(BaseModel):
    id: str
    lab_id: str | None
    user_id: str | None
    action: str
    status: str
    agent_id: str | None = None
    log_path: str | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class PermissionCreate(BaseModel):
    user_email: EmailStr
    role: str = "viewer"


class PermissionOut(BaseModel):
    id: str
    lab_id: str
    user_id: str
    role: str
    created_at: datetime
    user_email: EmailStr | None = None

    class Config:
        from_attributes = True
