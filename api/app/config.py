from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./netlab_gui.db"
    redis_url: str = "redis://redis:6379/0"
    netlab_workspace: str = "/var/lib/netlab-gui"
    qcow2_store: str | None = None
    log_forward_url: str | None = None
    netlab_provider: str = "clab"

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

    admin_email: str | None = None
    admin_password: str | None = None

    # CORS configuration
    cors_allowed_origins: str = "http://localhost:8090,http://127.0.0.1:8090"

    # Agent communication timeouts (seconds)
    # Deploy timeout increased to 900s (15 min) for VMs and slow cEOS boots
    agent_deploy_timeout: float = 900.0
    # Destroy timeout increased to 300s (5 min) for graceful shutdown
    agent_destroy_timeout: float = 300.0
    agent_node_action_timeout: float = 60.0
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

    # Job health monitoring settings
    # How often the job health monitor checks for stuck jobs (seconds)
    job_health_check_interval: int = 30
    # Maximum number of automatic retry attempts for failed jobs
    job_max_retries: int = 2
    # Job timeout for deploy operations (seconds) - buffer above agent_deploy_timeout
    job_timeout_deploy: int = 1200  # 20 minutes
    # Job timeout for destroy operations (seconds) - buffer above agent_destroy_timeout
    job_timeout_destroy: int = 600  # 10 minutes
    # Job timeout for sync operations (seconds)
    job_timeout_sync: int = 600  # 10 minutes
    # Job timeout for node start/stop operations (seconds)
    job_timeout_node: int = 300  # 5 minutes
    # Grace period after timeout before allowing reconciliation (seconds)
    job_stuck_grace_period: int = 60  # 1 minute

    # Feature flags
    feature_multihost_labs: bool = True
    feature_vxlan_overlay: bool = True


settings = Settings()
