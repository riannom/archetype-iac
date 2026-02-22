"""Console access and CLI verification schemas."""

from pydantic import BaseModel, Field


class ConsoleRequest(BaseModel):
    """Request to open console to a node."""
    lab_id: str
    node_name: str
    shell: str = "/bin/sh"


class ConsoleInfo(BaseModel):
    """Info needed to connect to console WebSocket."""
    websocket_path: str
    session_id: str


class CliVerifyRequest(BaseModel):
    """Request to run verification CLI commands on a node."""
    commands: list[str] = Field(default_factory=list)
    kind: str | None = None
    username: str | None = None
    password: str | None = None
    enable_password: str | None = None
    prompt_pattern: str | None = None
    paging_disable: str | None = None
    attempt_enable: bool = True
    timeout: int | None = Field(None, ge=5, le=600)
    retries: int = Field(default=2, ge=0, le=5)


class CliCommandOutput(BaseModel):
    """Single CLI command capture result."""
    command: str
    success: bool
    output: str = ""
    error: str | None = None


class CliVerifyResponse(BaseModel):
    """Response from node CLI verification command execution."""
    success: bool
    provider: str
    node_name: str
    domain_name: str | None = None
    commands_run: int = 0
    outputs: list[CliCommandOutput] = Field(default_factory=list)
    error: str | None = None
