"""Agent configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Agent settings loaded from environment variables."""

    # Agent identity
    agent_id: str = ""  # Auto-generated if not set
    agent_name: str = "default"
    agent_host: str = "0.0.0.0"
    agent_port: int = 8001
    is_local: bool = False  # True if co-located with controller (enables rebuild)
    advertise_host: str = ""  # Optional override for registration address

    # Controller connection
    controller_url: str = "http://localhost:8000"
    registration_token: str = ""  # Optional auth token for registration

    # Heartbeat settings
    heartbeat_interval: int = 10  # seconds

    # Provider capabilities
    enable_docker: bool = True  # Native Docker provider for container management
    enable_libvirt: bool = False  # Libvirt provider for qcow2 VMs

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

    # Docker client timeout (seconds) - covers container create, network ops, etc.
    # This needs to be long enough for slow operations like image layer extraction
    docker_client_timeout: int = 300  # 5 minutes for individual Docker API calls

    # Deploy operation timeouts (seconds)
    deploy_timeout: float = 900.0  # 15 minutes for deploy operations
    destroy_timeout: float = 300.0  # 5 minutes for destroy operations
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

    # Overlay MTU settings (fallback when path MTU discovery fails)
    # VXLAN adds ~50 bytes overhead (8 VXLAN + 8 UDP + 20 IP + 14 Ethernet)
    # Path MTU discovery runs automatically when creating VXLAN tunnels.
    # If discovery succeeds, tenant MTU = path_mtu - 50 (e.g., 9000 -> 8950, 1500 -> 1450)
    # If discovery fails, this fallback value is used.
    # Set to 0 to disable explicit MTU configuration (inherit from system)
    overlay_mtu: int = 1450  # Fallback MTU for tenant interfaces on overlay links

    # OVS networking (hot-plug support)
    enable_ovs: bool = True  # Enable OVS-based networking for hot-plug
    ovs_bridge_name: str = "arch-ovs"  # Name of OVS bridge
    ovs_vlan_start: int = 100  # Starting VLAN for port isolation
    ovs_vlan_end: int = 4000  # Ending VLAN for port isolation

    # Local link MTU (veth pairs on same host)
    # Default to jumbo frames since veth pairs are virtual and have no physical limits
    # Set to 0 to disable explicit MTU configuration (inherit system default of 1500)
    local_mtu: int = 9000  # MTU for local L2 links (veth pairs)

    # OVS Docker plugin (pre-boot interface provisioning)
    # When enabled, uses Docker network plugin for interface provisioning
    # This ensures interfaces exist BEFORE container init runs (required for cEOS)
    enable_ovs_plugin: bool = True  # Enable OVS Docker network plugin

    # Concurrency limits
    max_concurrent_jobs: int = 4

    # Lock management
    # Threshold for controller to consider a lock "stuck" (should match deploy_timeout)
    lock_stuck_threshold: float = 900.0  # 15 minutes - aligned with deploy_timeout

    # Logging configuration
    log_format: str = "json"  # "json" or "text"
    log_level: str = "INFO"

    # === Docker OVS Plugin Settings ===

    # Management network settings (eth0 for containers)
    mgmt_network_subnet_base: str = "172.20.0.0/16"  # /24 allocated per lab
    mgmt_network_enable_nat: bool = True

    # VXLAN settings for shared bridge (multi-host support)
    plugin_vxlan_vni_base: int = 200000  # Different range from overlay.py
    plugin_vxlan_vni_max: int = 299999
    plugin_vxlan_dst_port: int = 4789

    # Lab TTL cleanup settings
    lab_ttl_enabled: bool = False  # Disabled by default for safety
    lab_ttl_seconds: int = 86400  # 24 hours
    lab_ttl_check_interval: int = 3600  # Check every hour

    class Config:
        env_prefix = "ARCHETYPE_AGENT_"


settings = Settings()
