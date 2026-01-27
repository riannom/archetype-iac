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

    # Overlay networking
    enable_vxlan: bool = True  # Enable VXLAN overlay for multi-host
    local_ip: str = ""  # Local IP for VXLAN endpoints (auto-detect if empty)

    # Docker settings
    docker_socket: str = "unix:///var/run/docker.sock"

    # Workspace for lab files
    workspace_path: str = "/var/lib/archetype-agent"

    # Communication timeouts (seconds)
    registration_timeout: float = 10.0
    heartbeat_timeout: float = 5.0

    # Console I/O timeouts (seconds)
    console_read_timeout: float = 0.05
    console_input_timeout: float = 0.1

    # Container operations
    container_stop_timeout: int = 10

    # VXLAN networking
    vxlan_vni_base: int = 100000
    vxlan_vni_max: int = 199999

    # Concurrency limits
    max_concurrent_jobs: int = 4

    class Config:
        env_prefix = "ARCHETYPE_AGENT_"


settings = Settings()
