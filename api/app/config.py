from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://archetype:archetype@postgres:5432/archetype"
    redis_url: str = "redis://redis:6379/0"
    # Workspace directory for lab files and images
    workspace: str = "/var/lib/archetype"
    qcow2_store: str | None = None
    # Directory for ISO uploads (files placed here appear in ISO import browser)
    iso_upload_dir: str = "/var/lib/archetype/uploads"
    # vrnetlab repository path for building VM images
    vrnetlab_path: str = "/opt/vrnetlab"
    log_forward_url: str | None = None
    provider: str = "docker"

    local_auth_enabled: bool = True
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_app_redirect_url: str | None = None

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    session_secret: str = ""

    max_concurrent_jobs_per_user: int = 2

    admin_username: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    oidc_default_role: str = "viewer"

    # CORS configuration
    cors_allowed_origins: str = "http://localhost:8090,http://127.0.0.1:8090"

    # Internal URL for agent callbacks (how agents reach this controller)
    internal_url: str = "http://localhost:8000"

    # Agent communication timeouts (seconds)
    # Deploy timeout increased to 900s (15 min) for VMs and slow cEOS boots
    agent_deploy_timeout: float = 900.0
    # Destroy timeout increased to 300s (5 min) for graceful shutdown
    agent_destroy_timeout: float = 300.0
    agent_status_timeout: float = 30.0
    agent_health_check_timeout: float = 5.0

    # Retry configuration
    agent_max_retries: int = 3
    agent_retry_backoff_base: float = 1.0
    agent_retry_backoff_max: float = 10.0

    # Background tasks
    agent_health_check_interval: int = 30
    agent_stale_timeout: int = 90
    agent_cache_ttl: int = 30

    # State reconciliation settings
    # How often the reconciliation task runs (seconds)
    reconciliation_interval: int = 30
    # How long a node can be "pending" before auto-reconcile (seconds)
    stale_pending_threshold: int = 600  # 10 minutes
    # How long a lab can be "starting" before auto-reconcile (seconds)
    stale_starting_threshold: int = 900  # 15 minutes
    # How often image reconciliation runs (seconds)
    image_reconciliation_interval: int = 300  # 5 minutes

    # State enforcement settings
    # Enable automatic correction of desired vs actual state mismatches
    state_enforcement_enabled: bool = True
    # How often the enforcement task runs (seconds)
    state_enforcement_interval: int = 60
    # Cooldown before retrying enforcement on the same node (seconds)
    state_enforcement_cooldown: int = 300  # 5 minutes
    # Maximum number of enforcement retry attempts before giving up
    state_enforcement_max_retries: int = 3
    # Base backoff time for retries (seconds), doubles each attempt
    state_enforcement_retry_backoff: int = 60
    # Cooldown after a node crashes or fails before auto-restart (seconds)
    state_enforcement_crash_cooldown: int = 600  # 10 minutes
    # Enable auto-restart of crashed nodes (set to False to require manual intervention)
    state_enforcement_auto_restart_enabled: bool = True

    # Job health monitoring settings
    # How often the job health monitor checks for stuck jobs (seconds)
    job_health_check_interval: int = 30
    # Maximum number of automatic retry attempts for failed jobs
    job_max_retries: int = 2
    # Job timeout for deploy operations (seconds)
    # Must be > agent lock_ttl (960s) to allow lock to expire before job timeout
    # Using 1200s (20 min) gives 240s (4 min) margin over lock_ttl for safety
    job_timeout_deploy: int = 1200  # 20 minutes - 4 min margin over lock_ttl
    # Job timeout for destroy operations (seconds) - buffer above agent_destroy_timeout
    job_timeout_destroy: int = 360  # 6 minutes
    # Job timeout for sync operations (seconds)
    job_timeout_sync: int = 300  # 5 minutes
    # Job timeout for node start/stop operations (seconds)
    job_timeout_node: int = 300  # 5 minutes
    # Grace period after timeout before allowing reconciliation (seconds)
    job_stuck_grace_period: int = 60  # 1 minute

    # Resource capacity validation
    resource_validation_enabled: bool = True
    resource_memory_buffer_mb: int = 2048
    resource_cpu_buffer_cores: float = 1.0
    resource_disk_buffer_gb: float = 20.0
    resource_memory_warning_pct: float = 80.0
    resource_memory_error_pct: float = 98.0
    resource_cpu_warning_pct: float = 80.0
    resource_cpu_error_pct: float = 95.0
    resource_disk_warning_pct: float = 85.0
    resource_disk_error_pct: float = 95.0

    # Placement scheduling
    placement_controller_reserve_mb: int = 4096    # Memory reserved on local agent for API/DB/Redis
    placement_weight_memory: float = 0.7           # Weight for available-memory ratio in scoring
    placement_weight_cpu: float = 0.3              # Weight for available-CPU ratio in scoring
    placement_local_penalty: float = 0.85          # Score multiplier for local agent (0.85 = 15% reduction)
    placement_scoring_enabled: bool = True         # Feature flag (False = legacy job-count sort)

    # Feature flags
    feature_multihost_labs: bool = True
    feature_vxlan_overlay: bool = True
    # Auto-extract configs before destroy operations
    feature_auto_extract_on_destroy: bool = True
    # Auto-extract configs before stop operations (unified lifecycle)
    feature_auto_extract_on_stop: bool = True
    # Auto-extract configs before enforcement restart of crashed/exited nodes
    feature_auto_extract_on_enforcement: bool = True
    # Per-node lifecycle: create/start/stop/destroy individual containers
    # When False, falls back to full topology deploy via agent
    per_node_lifecycle_enabled: bool = True

    # Logging configuration
    log_format: str = "json"  # "json" or "text"
    log_level: str = "INFO"
    # Loki URL for querying logs (when centralized logging is enabled)
    loki_url: str = "http://loki:3100"

    # Image synchronization settings
    # Enable/disable image sync feature
    image_sync_enabled: bool = True
    # ImageSyncJob pending timeout (seconds) - jobs in pending state longer than this are considered stuck
    image_sync_job_pending_timeout: int = 120  # 2 minutes
    # Fallback strategy when agent has no preference: push, pull, on_demand, disabled
    image_sync_fallback_strategy: str = "on_demand"
    # Always check for missing images before deployment
    image_sync_pre_deploy_check: bool = True
    # Maximum seconds for a single image sync operation
    image_sync_timeout: int = 600
    # Maximum concurrent sync operations per agent
    image_sync_max_concurrent: int = 2
    # Chunk size for streaming image data (1MB default)
    image_sync_chunk_size: int = 1048576

    # ISO import settings
    # Per-file extraction timeout (seconds) - large qcow2 files can take a while
    iso_extraction_timeout: int = 1800  # 30 minutes
    # Overall import job timeout (seconds)
    iso_import_timeout: int = 14400  # 4 hours
    # Docker load timeout for container images (seconds)
    iso_docker_load_timeout: int = 600  # 10 minutes

    # vrnetlab build settings
    # Build timeout (seconds) - VM images with install phases can take a while
    vrnetlab_build_timeout: int = 3600  # 60 minutes
    # Auto-trigger vrnetlab builds on qcow2 upload
    vrnetlab_auto_build: bool = True

    # Reconciliation thresholds (previously hardcoded)
    # How long a node can be in stopping operation before reconciliation recovers it (seconds)
    stale_stopping_threshold: int = 360  # 6 minutes
    # How long a node can be in starting operation before reconciliation recovers it (seconds)
    stale_node_starting_threshold: int = 360  # 6 minutes
    # Run full orphan check every N reconciliation cycles
    lab_orphan_check_multiplier: int = 10
    # Orphaned tunnel cleanup timeout (seconds) - tunnels stuck in cleanup state
    orphaned_tunnel_cleanup_timeout: int = 300  # 5 minutes
    # Cooldown before retrying link repair (seconds)
    link_repair_cooldown: int = 120  # 2 minutes

    # Disk cleanup settings
    # How often the cleanup task runs (seconds)
    cleanup_interval: int = 3600  # 1 hour
    # Orphaned .partial files older than this are deleted (seconds)
    cleanup_upload_file_age: int = 86400  # 24 hours
    # Stale upload sessions older than this are expired (seconds)
    cleanup_upload_session_age: int = 14400  # 4 hours
    # Stale ISO sessions older than this are expired (seconds)
    cleanup_iso_session_age: int = 86400  # 24 hours
    # Old completed/failed jobs older than this are deleted (days)
    cleanup_job_retention_days: int = 30
    # Old webhook deliveries older than this are deleted (days)
    cleanup_webhook_retention_days: int = 7
    # Old config snapshots older than this are deleted (days), 0 to disable
    cleanup_config_snapshot_retention_days: int = 90
    # Old ImageSyncJob records older than this are deleted (days)
    cleanup_image_sync_job_retention_days: int = 30
    # Old ISOImportJob records older than this are deleted (days)
    cleanup_iso_import_job_retention_days: int = 30
    # Old AgentUpdateJob records older than this are deleted (days)
    cleanup_agent_update_job_retention_days: int = 30
    # Enable orphaned lab workspace cleanup
    cleanup_orphaned_workspaces: bool = True
    # Enable orphaned QCOW2 image cleanup
    cleanup_orphaned_qcow2: bool = True

    # Docker cleanup (on agents)
    cleanup_docker_enabled: bool = True
    cleanup_docker_dangling_images: bool = True
    cleanup_docker_build_cache: bool = True
    cleanup_docker_unused_volumes: bool = False  # Conservative - may have data

    # Event-driven cleanup
    cleanup_event_driven_enabled: bool = True
    reconciliation_interval_extended: int = 120     # 2 min (safety-net when events active)
    state_enforcement_interval_extended: int = 120  # 2 min
    cleanup_interval_extended: int = 3600           # 1 hour (safety-net for lost events)

    def get_interval(self, name: str) -> int:
        """Get monitor interval, using extended value when event-driven cleanup is active.

        State intervals (reconciliation, enforcement) never extend — they are
        the safety net for state convergence.  Only cleanup intervals extend
        when event-driven mode is active.
        """
        intervals = {
            "reconciliation": (self.reconciliation_interval, self.reconciliation_interval),
            "state_enforcement": (self.state_enforcement_interval, self.state_enforcement_interval),
            "cleanup": (self.cleanup_interval, self.cleanup_interval_extended),
        }
        normal, extended = intervals[name]
        return extended if self.cleanup_event_driven_enabled else normal

    # Database connection pool tuning
    # API process: more connections, fail fast for user requests
    # Scheduler process: fewer connections, can wait longer
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Version checking
    github_repo: str = "riannom/archetype-iac"
    version_check_cache_ttl: int = 3600  # 1 hour cache


settings = Settings()
