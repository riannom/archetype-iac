"""Shared pytest fixtures for API tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db, models
from app.auth import create_access_token, hash_password
from app.config import settings
from app.main import app


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
def test_client(test_db: Session, monkeypatch):
    """Create a FastAPI test client with database override."""
    # Ensure JWT secret is set for testing
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-key-for-testing")
    monkeypatch.setattr(settings, "local_auth_enabled", True)

    def override_get_db():
        try:
            yield test_db
        finally:
            pass  # Session cleanup handled by test_db fixture

    app.dependency_overrides[db.get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_user(test_db: Session) -> models.User:
    """Create a regular test user."""
    user = models.User(
        email="testuser@example.com",
        hashed_password=hash_password("testpassword123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> models.User:
    """Create an admin test user."""
    user = models.User(
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        is_active=True,
        is_admin=True,
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

    host = models.Host(
        id="test-agent-1",
        name="Test Agent",
        address="localhost:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
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
