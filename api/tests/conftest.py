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
        provider="containerlab",
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
        capabilities=json.dumps({"providers": ["containerlab"]}),
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
                    "name": "clab-test-r1",
                    "status": "running",
                    "lab_prefix": "test",
                    "is_system": False,
                },
                {
                    "name": "clab-test-r2",
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
            capabilities=json.dumps({"providers": ["containerlab"]}),
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
            capabilities=json.dumps({"providers": ["containerlab"]}),
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
            capabilities=json.dumps({"providers": ["containerlab"]}),
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
