"""Agent configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Agent settings loaded from environment variables."""

    # Agent identity
    agent_id: str = ""  # Auto-generated if not set
    agent_name: str = "default"
    agent_host: str = "0.0.0.0"
    agent_port: int = 8001

    # Controller connection
    controller_url: str = "http://localhost:8000"
    registration_token: str = ""  # Optional auth token for registration

    # Heartbeat settings
    heartbeat_interval: int = 10  # seconds

    # Provider capabilities
    enable_containerlab: bool = True
    enable_libvirt: bool = False

    # Docker settings
    docker_socket: str = "unix:///var/run/docker.sock"

    # Workspace for lab files
    workspace_path: str = "/var/lib/aura-agent"

    class Config:
        env_prefix = "AURA_AGENT_"


settings = Settings()
