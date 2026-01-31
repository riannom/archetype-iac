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

    # Libvirt settings
    libvirt_uri: str = "qemu:///system"
    qcow2_store_path: str = ""  # Path to qcow2 image store (auto-detect if empty)

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
    # Note: With event-driven I/O, these are fallback timeouts only
    console_read_timeout: float = 0.005  # 5ms fallback (primary is event-driven)
    console_input_timeout: float = 0.01  # 10ms input check interval

    # Container operations
    container_stop_timeout: int = 10

    # Deploy operation timeouts (seconds)
    deploy_timeout: float = 900.0  # 15 minutes for containerlab deploy
    destroy_timeout: float = 300.0  # 5 minutes for containerlab destroy
    lock_acquire_timeout: float = 30.0  # Time to wait for deploy lock

    # Redis connection for distributed locks
    # Uses the same Redis as controller by default
    redis_url: str = "redis://redis:6379/0"

    # Lock TTL (seconds) - short TTL with periodic extension during active deploys
    # If agent crashes, lock expires quickly. Active deploys extend the lock.
    lock_ttl: int = 120  # 2 minutes - extended periodically during deploy

    # How often to extend the lock during active operations (seconds)
    # Should be less than lock_ttl to ensure lock doesn't expire mid-deploy
    lock_extend_interval: float = 30.0  # Extend every 30 seconds

    # VXLAN networking
    vxlan_vni_base: int = 100000
    vxlan_vni_max: int = 199999

    # Concurrency limits
    max_concurrent_jobs: int = 4

    # Lock management
    # Threshold for controller to consider a lock "stuck" (should match deploy_timeout)
    lock_stuck_threshold: float = 900.0  # 15 minutes - aligned with deploy_timeout

    # Logging configuration
    log_format: str = "json"  # "json" or "text"
    log_level: str = "INFO"

    class Config:
        env_prefix = "ARCHETYPE_AGENT_"


settings = Settings()
