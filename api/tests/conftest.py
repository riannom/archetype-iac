"""Shared pytest fixtures for API tests."""
# ruff: noqa: E402  -- sys.path setup must run before app imports
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import db, models
from app.auth import create_access_token, hash_password
from app.config import settings
from app.main import app


def pytest_sessionstart(session):
    """Fail fast on unsupported Python versions for API TestClient runs."""
    if sys.version_info >= (3, 13):
        pytest.exit(
            "API pytest is not supported on Python 3.13+ in this repo yet. "
            "Use Python 3.11 to run api/tests.",
            returncode=2,
        )


@pytest.fixture(scope="function")
def test_engine():
    """Create an in-memory SQLite database engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture(scope="function")
def test_db(test_engine):
    """Create a database session for testing."""
    TestingSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def test_client(test_db: Session, test_engine, monkeypatch, tmp_path):
    """Create a FastAPI test client with database override."""
    # Save originals for manual cleanup (object.__setattr__ bypasses pydantic)
    _originals = {
        "jwt_secret": settings.jwt_secret,
        "local_auth_enabled": settings.local_auth_enabled,
        "workspace": settings.workspace,
        "iso_upload_dir": settings.iso_upload_dir,
        "agent_secret": settings.agent_secret,
    }

    # Use object.__setattr__ to bypass pydantic model validation/interception
    # which can fail silently after many monkeypatch cycles in the full suite
    object.__setattr__(settings, "jwt_secret", "test-jwt-secret-key-for-testing")
    object.__setattr__(settings, "local_auth_enabled", True)
    # Most API tests call agent-facing routes without agent auth headers.
    # Disable agent secret by default for test-client runs unless a test
    # explicitly monkeypatches settings.agent_secret.
    object.__setattr__(settings, "agent_secret", "")
    object.__setattr__(settings, "workspace", str(tmp_path / "workspace"))
    object.__setattr__(settings, "iso_upload_dir", str(tmp_path / "uploads"))

    # Prevent app lifespan from connecting to real PostgreSQL.
    # Tables are already created by the test_engine fixture.
    # Patch alembic to no-op and db.engine to use the test engine.
    from alembic import command as alembic_command
    monkeypatch.setattr(alembic_command, "upgrade", lambda *a, **kw: None)
    monkeypatch.setattr(db, "engine", test_engine)

    # Clear admin credentials so lifespan doesn't try to seed via get_session
    object.__setattr__(settings, "admin_email", None)
    _originals["admin_email"] = None

    def override_get_db():
        try:
            yield test_db
        finally:
            pass  # Session cleanup handled by test_db fixture

    # Override db.get_session so WebSocket handlers (which use get_session()
    # instead of the FastAPI get_db dependency) also use the test database.
    @contextmanager
    def override_get_session():
        yield test_db

    monkeypatch.setattr(db, "get_session", override_get_session)

    # Also patch get_session where it's imported directly by other modules
    # (e.g., middleware imports it as `from app.db import get_session`)
    import app.middleware as _middleware_mod
    monkeypatch.setattr(_middleware_mod, "get_session", override_get_session)

    # Patch console router which also imports get_session directly
    import app.routers.console as _console_mod
    monkeypatch.setattr(_console_mod, "get_session", override_get_session)

    app.dependency_overrides[db.get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        # Restore settings
        for key, val in _originals.items():
            object.__setattr__(settings, key, val)


@pytest.fixture(scope="function")
def test_user(test_db: Session) -> models.User:
    """Create a regular test user."""
    user = models.User(
        username="testuser",
        email="testuser@example.com",
        hashed_password=hash_password("testpassword123"),
        is_active=True,
        global_role="operator",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> models.User:
    """Create an admin test user."""
    user = models.User(
        username="admin",
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        is_active=True,
        global_role="admin",
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def auth_headers(test_user: models.User, monkeypatch) -> dict[str, str]:
    """Create authentication headers for the test user."""
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")
    token = create_access_token(test_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def ws_token(test_user: models.User) -> str:
    """Create a JWT token string for WebSocket authentication."""
    return create_access_token(test_user.id)


@pytest.fixture(scope="function")
def agent_auth_headers(monkeypatch) -> dict[str, str]:
    """Create bearer auth headers for agent-facing endpoints."""
    monkeypatch.setattr(settings, "agent_secret", "test-agent-secret")
    return {"Authorization": "Bearer test-agent-secret"}


@pytest.fixture(scope="function")
def admin_auth_headers(admin_user: models.User, monkeypatch) -> dict[str, str]:
    """Create authentication headers for the admin user."""
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")
    token = create_access_token(admin_user.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def sample_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a sample lab for testing."""
    lab = models.Lab(
        name="Test Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/test-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture(scope="function")
def sample_lab_with_nodes(
    test_db: Session, sample_lab: models.Lab
) -> tuple[models.Lab, list[models.NodeState]]:
    """Create a sample lab with node states for testing."""
    nodes = [
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="R1",
            desired_state="stopped",
            actual_state="undeployed",
        ),
        models.NodeState(
            lab_id=sample_lab.id,
            node_id="r2",
            node_name="R2",
            desired_state="stopped",
            actual_state="undeployed",
        ),
    ]
    for node in nodes:
        test_db.add(node)
    test_db.commit()
    for node in nodes:
        test_db.refresh(node)
    return sample_lab, nodes


@pytest.fixture(scope="function")
def sample_host(test_db: Session) -> models.Host:
    """Create a sample agent host for testing."""
    import json
    from datetime import datetime, timezone

    host = models.Host(
        id="test-agent-1",
        name="Test Agent",
        address="localhost:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),  # Required for is_agent_online()
        resource_usage=json.dumps({
            "cpu_percent": 25.5,
            "memory_percent": 45.2,
            "disk_percent": 60.0,
            "disk_used_gb": 120.0,
            "disk_total_gb": 200.0,
            "containers_running": 5,
            "containers_total": 10,
            "container_details": [
                {
                    "name": "archetype-test-r1",
                    "status": "running",
                    "lab_prefix": "test",
                    "is_system": False,
                },
                {
                    "name": "archetype-test-r2",
                    "status": "running",
                    "lab_prefix": "test",
                    "is_system": False,
                },
            ],
        }),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture(scope="function")
def multiple_hosts(test_db: Session) -> list[models.Host]:
    """Create multiple agent hosts for multi-host testing."""
    import json

    hosts = [
        models.Host(
            id="agent-1",
            name="Agent 1",
            address="agent1.local:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 30.0,
                "memory_percent": 50.0,
                "disk_percent": 40.0,
                "disk_used_gb": 80.0,
                "disk_total_gb": 200.0,
                "containers_running": 3,
                "containers_total": 5,
                "container_details": [],
            }),
        ),
        models.Host(
            id="agent-2",
            name="Agent 2",
            address="agent2.local:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({
                "cpu_percent": 20.0,
                "memory_percent": 40.0,
                "disk_percent": 30.0,
                "disk_used_gb": 60.0,
                "disk_total_gb": 200.0,
                "containers_running": 2,
                "containers_total": 4,
                "container_details": [],
            }),
        ),
        models.Host(
            id="agent-3",
            name="Agent 3",
            address="agent3.local:8080",
            status="offline",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            resource_usage=json.dumps({}),
        ),
    ]
    for host in hosts:
        test_db.add(host)
    test_db.commit()
    for host in hosts:
        test_db.refresh(host)
    return hosts


@pytest.fixture(scope="function")
def offline_host(test_db: Session) -> models.Host:
    """Create an offline agent host for testing."""
    import json
    from datetime import datetime, timedelta, timezone

    host = models.Host(
        id="offline-agent",
        name="Offline Agent",
        address="offline.local:8080",
        status="offline",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        resource_usage=json.dumps({}),
        last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture(scope="function")
def sample_job(test_db: Session, sample_lab: models.Lab, test_user: models.User) -> models.Job:
    """Create a sample queued job for testing."""
    job = models.Job(
        id="test-job-1",
        lab_id=sample_lab.id,
        user_id=test_user.id,
        action="up",
        status="queued",
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture(scope="function")
def running_job(test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host) -> models.Job:
    """Create a running job for testing."""
    from datetime import datetime, timezone

    job = models.Job(
        id="running-job-1",
        lab_id=sample_lab.id,
        user_id=test_user.id,
        action="up",
        status="running",
        agent_id=sample_host.id,
        started_at=datetime.now(timezone.utc),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture(scope="function")
def stuck_queued_job(test_db: Session, sample_lab: models.Lab, test_user: models.User) -> models.Job:
    """Create a job that's been queued for too long (stuck)."""
    from datetime import datetime, timedelta, timezone

    job = models.Job(
        id="stuck-queued-job",
        lab_id=sample_lab.id,
        user_id=test_user.id,
        action="up",
        status="queued",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture(scope="function")
def stuck_running_job(test_db: Session, sample_lab: models.Lab, test_user: models.User, sample_host: models.Host) -> models.Job:
    """Create a job that's been running too long (stuck/timed out)."""
    from datetime import datetime, timedelta, timezone

    job = models.Job(
        id="stuck-running-job",
        lab_id=sample_lab.id,
        user_id=test_user.id,
        action="up",
        status="running",
        agent_id=sample_host.id,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=60),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture(scope="function")
def sample_image_host(test_db: Session, sample_host: models.Host) -> models.ImageHost:
    """Create a sample ImageHost record for testing."""
    from datetime import datetime, timezone

    image_host = models.ImageHost(
        id="test-image-host-1",
        image_id="docker:ceos:4.28.0F",
        host_id=sample_host.id,
        reference="ceos:4.28.0F",
        status="synced",
        synced_at=datetime.now(timezone.utc),
    )
    test_db.add(image_host)
    test_db.commit()
    test_db.refresh(image_host)
    return image_host


@pytest.fixture(scope="function")
def sample_image_sync_job(test_db: Session, sample_host: models.Host) -> models.ImageSyncJob:
    """Create a sample ImageSyncJob for testing."""
    job = models.ImageSyncJob(
        id="test-sync-job-1",
        image_id="docker:ceos:4.28.0F",
        host_id=sample_host.id,
        status="pending",
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture(scope="function")
def sample_link_state(test_db: Session, sample_lab: models.Lab) -> models.LinkState:
    """Create a sample LinkState for testing."""
    link = models.LinkState(
        id="test-link-1",
        lab_id=sample_lab.id,
        link_name="R1:eth1-R2:eth1",
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        desired_state="up",
        actual_state="unknown",
    )
    test_db.add(link)
    test_db.commit()
    test_db.refresh(link)
    return link


@pytest.fixture(scope="function")
def sample_cross_host_link_state(
    test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
) -> models.LinkState:
    """Create a sample cross-host LinkState for testing."""
    link = models.LinkState(
        id="cross-host-link-1",
        lab_id=sample_lab.id,
        link_name="R1:eth1-R3:eth1",
        source_node="R1",
        source_interface="eth1",
        target_node="R3",
        target_interface="eth1",
        desired_state="up",
        actual_state="pending",
        is_cross_host=True,
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    test_db.add(link)
    test_db.commit()
    test_db.refresh(link)
    return link


@pytest.fixture(scope="function")
def sample_vxlan_tunnel(
    test_db: Session, sample_lab: models.Lab, sample_cross_host_link_state: models.LinkState,
    multiple_hosts: list[models.Host]
) -> models.VxlanTunnel:
    """Create a sample VxlanTunnel for testing."""
    tunnel = models.VxlanTunnel(
        id="tunnel-1",
        lab_id=sample_lab.id,
        link_state_id=sample_cross_host_link_state.id,
        vni=12345,
        vlan_tag=200,
        agent_a_id=multiple_hosts[0].id,
        agent_a_ip="192.168.1.1",
        agent_b_id=multiple_hosts[1].id,
        agent_b_ip="192.168.1.2",
        status="active",
    )
    test_db.add(tunnel)
    test_db.commit()
    test_db.refresh(tunnel)
    return tunnel


@pytest.fixture(scope="function")
def sample_node_definitions(
    test_db: Session, sample_lab: models.Lab, sample_host: models.Host
) -> list[models.Node]:
    """Create sample Node definitions for testing."""
    nodes = [
        models.Node(
            id="node-def-1",
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="R1",
            container_name="archetype-test-r1",
            device="linux",
            host_id=sample_host.id,
        ),
        models.Node(
            id="node-def-2",
            lab_id=sample_lab.id,
            gui_id="n2",
            display_name="R2",
            container_name="archetype-test-r2",
            device="linux",
            host_id=sample_host.id,
        ),
    ]
    for node in nodes:
        test_db.add(node)
    test_db.commit()
    for node in nodes:
        test_db.refresh(node)
    return nodes


@pytest.fixture(scope="function")
def sample_link_definition(
    test_db: Session, sample_lab: models.Lab, sample_node_definitions: list[models.Node]
) -> models.Link:
    """Create a sample Link definition for testing."""
    link = models.Link(
        id="link-def-1",
        lab_id=sample_lab.id,
        link_name="R1:eth1-R2:eth1",
        source_node_id=sample_node_definitions[0].id,
        source_interface="eth1",
        target_node_id=sample_node_definitions[1].id,
        target_interface="eth1",
    )
    test_db.add(link)
    test_db.commit()
    test_db.refresh(link)
    return link


@pytest.fixture(scope="function")
def active_link_state_with_carrier(
    test_db: Session, sample_lab: models.Lab, sample_host: models.Host
) -> models.LinkState:
    """Create an active LinkState with carrier states for testing."""
    link = models.LinkState(
        id="active-link-with-carrier",
        lab_id=sample_lab.id,
        link_name="R1:eth1-R2:eth1",
        source_node="archetype-test-r1",
        source_interface="eth1",
        target_node="archetype-test-r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
        vlan_tag=100,
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
        source_carrier_state="on",
        target_carrier_state="on",
    )
    test_db.add(link)
    test_db.commit()
    test_db.refresh(link)
    return link


# --- Fixtures for Live Node Management Tests ---


@pytest.fixture(scope="function")
def mock_broadcaster():
    """Mock broadcaster for verifying publish calls.

    Use this fixture to verify that state changes are being broadcast
    correctly through the StateBroadcaster pub/sub system.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock = MagicMock()
    mock.publish_node_state = AsyncMock(return_value=1)
    mock.publish_link_state = AsyncMock(return_value=1)
    mock.publish_lab_state = AsyncMock(return_value=1)
    mock.publish_job_progress = AsyncMock(return_value=1)
    return mock


@pytest.fixture(scope="function")
def debouncer():
    """Fresh debouncer instance for testing.

    Creates a new NodeChangeDebouncer instance for each test,
    isolated from the global singleton.
    """
    from app.tasks.live_nodes import NodeChangeDebouncer

    return NodeChangeDebouncer()


@pytest.fixture(scope="function")
def running_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a lab in running state for live node tests."""
    lab = models.Lab(
        name="Running Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/running-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture(scope="function")
def stopped_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a lab in stopped state for live node tests."""
    lab = models.Lab(
        name="Stopped Lab",
        owner_id=test_user.id,
        provider="docker",
        state="stopped",
        workspace_path="/tmp/stopped-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture(scope="function")
def deployed_node_state(test_db: Session, running_lab: models.Lab) -> models.NodeState:
    """Create a deployed node state for testing destruction."""
    node = models.NodeState(
        lab_id=running_lab.id,
        node_id="n1",
        node_name="archetype-running-lab-r1",
        desired_state="running",
        actual_state="running",
        is_ready=True,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


@pytest.fixture(scope="function")
def undeployed_node_state(test_db: Session, running_lab: models.Lab) -> models.NodeState:
    """Create an undeployed node state for testing deployment."""
    node = models.NodeState(
        lab_id=running_lab.id,
        node_id="n2",
        node_name="archetype-running-lab-r2",
        desired_state="stopped",
        actual_state="undeployed",
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node
