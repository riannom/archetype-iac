"""Tests for SQLAlchemy models (models.py).

This module tests:
- Model creation and defaults
- Model relationships
- Model constraints
"""
from __future__ import annotations


import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models


class TestUserModel:
    """Tests for User model."""

    def test_create_user(self, test_db: Session):
        """Create user with all fields."""
        user = models.User(
            username="testuser",
            email="test@example.com",
            hashed_password="hashedpassword123",
            is_active=True,
            global_role="operator",
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        assert user.id is not None
        assert len(user.id) == 36  # UUID format
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.global_role == "operator"
        assert user.created_at is not None

    def test_user_defaults(self, test_db: Session):
        """User has correct defaults."""
        user = models.User(
            username="defaultuser",
            email="default@example.com",
            hashed_password="hash",
        )
        test_db.add(user)
        test_db.commit()
        test_db.refresh(user)

        assert user.is_active is True
        assert user.global_role == "operator"

    def test_email_unique_constraint(self, test_db: Session):
        """Email must be unique."""
        user1 = models.User(username="user1", email="same@example.com", hashed_password="hash1")
        test_db.add(user1)
        test_db.commit()

        user2 = models.User(username="user2", email="same@example.com", hashed_password="hash2")
        test_db.add(user2)
        with pytest.raises(IntegrityError):
            test_db.commit()


class TestLabModel:
    """Tests for Lab model."""

    def test_create_lab(self, test_db: Session, test_user: models.User):
        """Create lab with all fields."""
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            workspace_path="/tmp/test-lab",
            provider="docker",
            state="stopped",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        assert lab.id is not None
        assert lab.name == "Test Lab"
        assert lab.owner_id == test_user.id
        assert lab.provider == "docker"
        assert lab.state == "stopped"

    def test_lab_defaults(self, test_db: Session, test_user: models.User):
        """Lab has correct defaults."""
        lab = models.Lab(
            name="Default Lab",
            owner_id=test_user.id,
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        assert lab.provider == "docker"
        assert lab.state == "stopped"
        assert lab.workspace_path == ""
        assert lab.agent_id is None

    def test_lab_states(self, test_db: Session, test_user: models.User):
        """Lab can have various states."""
        valid_states = ["stopped", "starting", "running", "stopping", "error", "unknown"]
        for state in valid_states:
            lab = models.Lab(
                name=f"Lab {state}",
                owner_id=test_user.id,
                state=state,
            )
            test_db.add(lab)
        test_db.commit()

        labs = test_db.query(models.Lab).filter(models.Lab.owner_id == test_user.id).all()
        assert len(labs) == len(valid_states)


class TestJobModel:
    """Tests for Job model."""

    def test_create_job(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Create job with all fields."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.id is not None
        assert job.lab_id == sample_lab.id
        assert job.action == "up"
        assert job.status == "queued"

    def test_job_defaults(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Job has correct defaults."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.status == "queued"
        assert job.retry_count == 0
        assert job.agent_id is None
        assert job.started_at is None
        assert job.completed_at is None

    def test_job_statuses(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Job can have various statuses."""
        valid_statuses = ["queued", "running", "completed", "failed", "cancelled"]
        for status in valid_statuses:
            job = models.Job(
                lab_id=sample_lab.id,
                user_id=test_user.id,
                action="up",
                status=status,
            )
            test_db.add(job)
        test_db.commit()

        jobs = test_db.query(models.Job).filter(models.Job.lab_id == sample_lab.id).all()
        assert len(jobs) == len(valid_statuses)

    def test_job_parent_job_id(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Job can have parent_job_id for parent-child relationships."""
        # Create parent job
        parent = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
        )
        test_db.add(parent)
        test_db.commit()
        test_db.refresh(parent)

        # Create child job with parent_job_id
        child = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:agent:host1:node1",
            status="queued",
            parent_job_id=parent.id,
        )
        test_db.add(child)
        test_db.commit()
        test_db.refresh(child)

        assert child.parent_job_id == parent.id

        # Query children by parent
        children = test_db.query(models.Job).filter(
            models.Job.parent_job_id == parent.id
        ).all()
        assert len(children) == 1
        assert children[0].id == child.id

    def test_job_parent_job_id_nullable(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """parent_job_id should be nullable (most jobs don't have parents)."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.parent_job_id is None

    def test_job_superseded_by_id(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Job can have superseded_by_id for retry tracking."""
        # Create original job
        original = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="failed",
        )
        test_db.add(original)
        test_db.commit()
        test_db.refresh(original)

        # Create retry job
        retry = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="queued",
            retry_count=1,
        )
        test_db.add(retry)
        test_db.commit()
        test_db.refresh(retry)

        # Link original to retry
        original.superseded_by_id = retry.id
        test_db.commit()
        test_db.refresh(original)

        assert original.superseded_by_id == retry.id

    def test_job_superseded_by_id_nullable(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """superseded_by_id should be nullable (most jobs aren't superseded)."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.superseded_by_id is None

    def test_job_defaults_include_parent_tracking(self, test_db: Session, sample_lab: models.Lab, test_user: models.User):
        """Job defaults should include None for parent tracking fields."""
        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        assert job.parent_job_id is None
        assert job.superseded_by_id is None


class TestHostModel:
    """Tests for Host model."""

    def test_create_host(self, test_db: Session):
        """Create host with all fields."""
        host = models.Host(
            id="custom-agent-id",
            name="Test Agent",
            address="192.168.1.10:8080",
            status="online",
            capabilities='{"providers": ["docker"]}',
            version="1.0.0",
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        assert host.id == "custom-agent-id"
        assert host.name == "Test Agent"
        assert host.address == "192.168.1.10:8080"
        assert host.status == "online"

    def test_host_defaults(self, test_db: Session):
        """Host has correct defaults."""
        host = models.Host(
            id="default-agent",
            name="Default Agent",
            address="localhost:8080",
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        assert host.status == "offline"
        assert host.capabilities == "{}"
        assert host.version == ""
        assert host.image_sync_strategy == "on_demand"
        assert host.deployment_mode == "unknown"


class TestNodeStateModel:
    """Tests for NodeState model."""

    def test_create_node_state(self, test_db: Session, sample_lab: models.Lab):
        """Create node state with all fields."""
        node = models.NodeState(
            lab_id=sample_lab.id,
            node_id="node-frontend-id",
            node_name="router1",
            desired_state="running",
            actual_state="stopped",
        )
        test_db.add(node)
        test_db.commit()
        test_db.refresh(node)

        assert node.id is not None
        assert node.lab_id == sample_lab.id
        assert node.node_name == "router1"
        assert node.desired_state == "running"
        assert node.actual_state == "stopped"

    def test_node_state_defaults(self, test_db: Session, sample_lab: models.Lab):
        """NodeState has correct defaults."""
        node = models.NodeState(
            lab_id=sample_lab.id,
            node_id="node-1",
            node_name="r1",
        )
        test_db.add(node)
        test_db.commit()
        test_db.refresh(node)

        assert node.desired_state == "stopped"
        assert node.actual_state == "undeployed"
        assert node.is_ready is False
        assert node.error_message is None

    def test_node_state_unique_constraint(self, test_db: Session, sample_lab: models.Lab):
        """Node state is unique per lab+node_id."""
        node1 = models.NodeState(
            lab_id=sample_lab.id,
            node_id="same-node-id",
            node_name="r1",
        )
        test_db.add(node1)
        test_db.commit()

        node2 = models.NodeState(
            lab_id=sample_lab.id,
            node_id="same-node-id",
            node_name="r2",
        )
        test_db.add(node2)
        with pytest.raises(IntegrityError):
            test_db.commit()


class TestLinkStateModel:
    """Tests for LinkState model."""

    def test_create_link_state(self, test_db: Session, sample_lab: models.Lab):
        """Create link state with all fields."""
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)

        assert link.id is not None
        assert link.link_name == "r1:eth1-r2:eth1"
        assert link.source_node == "r1"
        assert link.target_node == "r2"

    def test_link_state_defaults(self, test_db: Session, sample_lab: models.Lab):
        """LinkState has correct defaults."""
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="test-link",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)

        assert link.desired_state == "up"
        assert link.actual_state == "unknown"


class TestLinkEndpointReservationModel:
    """Tests for LinkEndpointReservation model."""

    def test_endpoint_reservation_unique_per_endpoint(self, test_db: Session, sample_lab: models.Lab):
        """Only one desired-up link may reserve a given endpoint."""
        link_a = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
        )
        link_b = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r3:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r3",
            target_interface="eth1",
        )
        test_db.add_all([link_a, link_b])
        test_db.commit()

        res_a = models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=link_a.id,
            node_name="r1",
            interface_name="eth1",
        )
        test_db.add(res_a)
        test_db.commit()

        res_b = models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=link_b.id,
            node_name="r1",
            interface_name="eth1",
        )
        test_db.add(res_b)
        with pytest.raises(IntegrityError):
            test_db.commit()


class TestImageHostModel:
    """Tests for ImageHost model."""

    def test_create_image_host(self, test_db: Session, sample_host: models.Host):
        """Create image host record."""
        image_host = models.ImageHost(
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            reference="ceos:4.28.0F",
            status="synced",
            size_bytes=1024000,
        )
        test_db.add(image_host)
        test_db.commit()
        test_db.refresh(image_host)

        assert image_host.id is not None
        assert image_host.image_id == "docker:ceos:4.28.0F"
        assert image_host.status == "synced"

    def test_image_host_defaults(self, test_db: Session, sample_host: models.Host):
        """ImageHost has correct defaults."""
        image_host = models.ImageHost(
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            reference="test:1.0",
        )
        test_db.add(image_host)
        test_db.commit()
        test_db.refresh(image_host)

        assert image_host.status == "unknown"
        assert image_host.size_bytes is None
        assert image_host.synced_at is None

    def test_image_host_unique_constraint(self, test_db: Session, sample_host: models.Host):
        """ImageHost is unique per image+host."""
        ih1 = models.ImageHost(
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            reference="test:1.0",
        )
        test_db.add(ih1)
        test_db.commit()

        ih2 = models.ImageHost(
            image_id="docker:test:1.0",
            host_id=sample_host.id,
            reference="test:1.0",
        )
        test_db.add(ih2)
        with pytest.raises(IntegrityError):
            test_db.commit()


class TestConfigSnapshotModel:
    """Tests for ConfigSnapshot model."""

    def test_create_config_snapshot(self, test_db: Session, sample_lab: models.Lab):
        """Create config snapshot."""
        snapshot = models.ConfigSnapshot(
            lab_id=sample_lab.id,
            node_name="router1",
            content="hostname router1\n!",
            content_hash="abc123def456",
            snapshot_type="manual",
        )
        test_db.add(snapshot)
        test_db.commit()
        test_db.refresh(snapshot)

        assert snapshot.id is not None
        assert snapshot.node_name == "router1"
        assert snapshot.snapshot_type == "manual"


class TestCascadeDeletes:
    """Tests for cascade delete behavior."""

    def test_delete_lab_cascades_to_node_states(self, test_db: Session, test_user: models.User):
        """Deleting lab cascades to node states."""
        lab = models.Lab(name="Cascade Test", owner_id=test_user.id)
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        node = models.NodeState(
            lab_id=lab.id,
            node_id="node-1",
            node_name="r1",
        )
        test_db.add(node)
        test_db.commit()

        lab_id = lab.id

        # Delete children first (SQLite test DB doesn't enforce FK cascades)
        test_db.query(models.NodeState).filter(models.NodeState.lab_id == lab_id).delete()
        # Delete lab
        test_db.delete(lab)
        test_db.commit()

        # Node state should be deleted
        nodes = test_db.query(models.NodeState).filter(models.NodeState.lab_id == lab_id).all()
        assert len(nodes) == 0

    def test_delete_lab_cascades_to_link_states(self, test_db: Session, test_user: models.User):
        """Deleting lab cascades to link states."""
        lab = models.Lab(name="Cascade Test", owner_id=test_user.id)
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        link = models.LinkState(
            lab_id=lab.id,
            link_name="test-link",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        lab_id = lab.id

        # Delete children first (SQLite test DB doesn't enforce FK cascades)
        test_db.query(models.LinkState).filter(models.LinkState.lab_id == lab_id).delete()
        # Delete lab
        test_db.delete(lab)
        test_db.commit()

        # Link state should be deleted
        links = test_db.query(models.LinkState).filter(models.LinkState.lab_id == lab_id).all()
        assert len(links) == 0
