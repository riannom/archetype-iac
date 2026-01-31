"""Tests for Pydantic schemas (schemas.py).

This module tests:
- Schema validation
- Field defaults
- Model conversion
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import schemas


class TestLabSchemas:
    """Tests for Lab schemas."""

    def test_lab_create_minimal(self):
        """LabCreate with minimal fields."""
        lab = schemas.LabCreate(name="Test Lab")
        assert lab.name == "Test Lab"
        assert lab.provider == "docker"

    def test_lab_create_with_provider(self):
        """LabCreate with custom provider."""
        lab = schemas.LabCreate(name="Test Lab", provider="libvirt")
        assert lab.provider == "libvirt"

    def test_lab_update_partial(self):
        """LabUpdate allows partial updates."""
        update = schemas.LabUpdate(name="New Name")
        assert update.name == "New Name"

        update = schemas.LabUpdate()
        assert update.name is None

    def test_lab_out_from_model(self):
        """LabOut validates from model attributes."""
        data = {
            "id": "lab-123",
            "name": "Test Lab",
            "owner_id": "user-456",
            "workspace_path": "/tmp/lab",
            "provider": "docker",
            "state": "running",
            "created_at": datetime.now(timezone.utc),
        }
        lab = schemas.LabOut(**data)
        assert lab.id == "lab-123"
        assert lab.state == "running"


class TestUserSchemas:
    """Tests for User schemas."""

    def test_user_create_valid(self):
        """UserCreate with valid data."""
        user = schemas.UserCreate(
            email="test@example.com",
            password="securepassword123"
        )
        assert user.email == "test@example.com"

    def test_user_create_invalid_email(self):
        """UserCreate rejects invalid email."""
        with pytest.raises(ValidationError):
            schemas.UserCreate(email="not-an-email", password="password123")

    def test_user_create_short_password(self):
        """UserCreate rejects short password."""
        with pytest.raises(ValidationError):
            schemas.UserCreate(email="test@example.com", password="short")

    def test_user_create_long_password(self):
        """UserCreate rejects overly long password."""
        with pytest.raises(ValidationError):
            schemas.UserCreate(
                email="test@example.com",
                password="a" * 100  # Exceeds max_length=72
            )

    def test_user_out_from_model(self):
        """UserOut validates from model attributes."""
        data = {
            "id": "user-123",
            "email": "test@example.com",
            "is_active": True,
            "is_admin": False,
            "created_at": datetime.now(timezone.utc),
        }
        user = schemas.UserOut(**data)
        assert user.id == "user-123"
        assert user.is_active is True


class TestTopologySchemas:
    """Tests for topology-related schemas."""

    def test_graph_endpoint_minimal(self):
        """GraphEndpoint with minimal fields."""
        endpoint = schemas.GraphEndpoint(node="r1")
        assert endpoint.node == "r1"
        assert endpoint.type == "node"
        assert endpoint.ifname is None

    def test_graph_endpoint_with_ip(self):
        """GraphEndpoint with IP address."""
        endpoint = schemas.GraphEndpoint(
            node="r1",
            ifname="eth1",
            ipv4="10.0.0.1/24"
        )
        assert endpoint.ipv4 == "10.0.0.1/24"

    def test_graph_endpoint_external_types(self):
        """GraphEndpoint supports external connection types."""
        for conn_type in ["node", "bridge", "macvlan", "host"]:
            endpoint = schemas.GraphEndpoint(node="br-prod", type=conn_type)
            assert endpoint.type == conn_type

    def test_graph_link(self):
        """GraphLink with endpoints."""
        link = schemas.GraphLink(
            endpoints=[
                schemas.GraphEndpoint(node="r1", ifname="eth1"),
                schemas.GraphEndpoint(node="r2", ifname="eth1"),
            ]
        )
        assert len(link.endpoints) == 2
        assert link.type is None

    def test_graph_link_with_properties(self):
        """GraphLink with optional properties."""
        link = schemas.GraphLink(
            endpoints=[
                schemas.GraphEndpoint(node="r1", ifname="eth1"),
                schemas.GraphEndpoint(node="r2", ifname="eth1"),
            ],
            mtu=9000,
            bandwidth=1000,
        )
        assert link.mtu == 9000
        assert link.bandwidth == 1000

    def test_graph_node_device(self):
        """GraphNode for device."""
        node = schemas.GraphNode(
            id="node-1",
            name="Router1",
            device="eos",
            image="ceos:4.28.0F",
        )
        assert node.id == "node-1"
        assert node.name == "Router1"
        assert node.node_type == "device"

    def test_graph_node_external(self):
        """GraphNode for external network."""
        node = schemas.GraphNode(
            id="ext-1",
            name="Production VLAN",
            node_type="external",
            connection_type="vlan",
            parent_interface="ens192",
            vlan_id=100,
        )
        assert node.node_type == "external"
        assert node.vlan_id == 100

    def test_topology_graph(self):
        """TopologyGraph with nodes and links."""
        graph = schemas.TopologyGraph(
            nodes=[
                schemas.GraphNode(id="1", name="r1", device="linux"),
                schemas.GraphNode(id="2", name="r2", device="linux"),
            ],
            links=[
                schemas.GraphLink(
                    endpoints=[
                        schemas.GraphEndpoint(node="r1", ifname="eth1"),
                        schemas.GraphEndpoint(node="r2", ifname="eth1"),
                    ]
                )
            ],
        )
        assert len(graph.nodes) == 2
        assert len(graph.links) == 1


class TestMultiHostSchemas:
    """Tests for multi-host deployment schemas."""

    def test_node_placement(self):
        """NodePlacement schema."""
        placement = schemas.NodePlacement(
            node_name="router1",
            host_id="agent-123",
        )
        assert placement.node_name == "router1"
        assert placement.host_id == "agent-123"

    def test_cross_host_link(self):
        """CrossHostLink schema."""
        link = schemas.CrossHostLink(
            link_id="r1:eth1-r2:eth1",
            node_a="r1",
            interface_a="eth1",
            host_a="agent-1",
            node_b="r2",
            interface_b="eth1",
            host_b="agent-2",
        )
        assert link.link_id == "r1:eth1-r2:eth1"
        assert link.host_a == "agent-1"
        assert link.host_b == "agent-2"

    def test_cross_host_link_with_ips(self):
        """CrossHostLink with IP addresses."""
        link = schemas.CrossHostLink(
            link_id="link-1",
            node_a="r1",
            interface_a="eth1",
            host_a="agent-1",
            ip_a="10.0.0.1/24",
            node_b="r2",
            interface_b="eth1",
            host_b="agent-2",
            ip_b="10.0.0.2/24",
        )
        assert link.ip_a == "10.0.0.1/24"
        assert link.ip_b == "10.0.0.2/24"

    def test_topology_analysis(self):
        """TopologyAnalysis schema."""
        analysis = schemas.TopologyAnalysis(
            placements={
                "agent-1": [schemas.NodePlacement(node_name="r1", host_id="agent-1")],
                "agent-2": [schemas.NodePlacement(node_name="r2", host_id="agent-2")],
            },
            cross_host_links=[
                schemas.CrossHostLink(
                    link_id="link-1",
                    node_a="r1",
                    interface_a="eth1",
                    host_a="agent-1",
                    node_b="r2",
                    interface_b="eth1",
                    host_b="agent-2",
                )
            ],
            single_host=False,
        )
        assert len(analysis.placements) == 2
        assert len(analysis.cross_host_links) == 1
        assert analysis.single_host is False


class TestJobSchemas:
    """Tests for Job schemas."""

    def test_job_out_minimal(self):
        """JobOut with minimal fields."""
        job = schemas.JobOut(
            id="job-123",
            lab_id="lab-456",
            user_id="user-789",
            action="up",
            status="queued",
            log_path=None,
            created_at=datetime.now(timezone.utc),
        )
        assert job.id == "job-123"
        assert job.action == "up"
        assert job.status == "queued"

    def test_job_out_with_derived_fields(self):
        """JobOut with derived fields."""
        now = datetime.now(timezone.utc)
        job = schemas.JobOut(
            id="job-123",
            lab_id="lab-456",
            user_id="user-789",
            action="up",
            status="failed",
            log_path="Error: deployment failed",
            created_at=now,
            started_at=now,
            completed_at=now,
            timeout_at=now,
            is_stuck=True,
            error_summary="Deployment failed: image not found",
        )
        assert job.is_stuck is True
        assert job.error_summary is not None

    def test_job_out_with_image_sync_events(self):
        """JobOut with image sync events."""
        job = schemas.JobOut(
            id="job-123",
            lab_id="lab-456",
            user_id="user-789",
            action="up",
            status="queued",
            log_path=None,
            created_at=datetime.now(timezone.utc),
            image_sync_events=["Syncing ceos:4.28.0F to agent-1", "Sync complete"],
        )
        assert len(job.image_sync_events) == 2


class TestPermissionSchemas:
    """Tests for Permission schemas."""

    def test_permission_create(self):
        """PermissionCreate with valid data."""
        perm = schemas.PermissionCreate(
            user_email="collab@example.com",
            role="editor",
        )
        assert perm.user_email == "collab@example.com"
        assert perm.role == "editor"

    def test_permission_create_default_role(self):
        """PermissionCreate defaults to viewer role."""
        perm = schemas.PermissionCreate(user_email="viewer@example.com")
        assert perm.role == "viewer"

    def test_permission_create_invalid_email(self):
        """PermissionCreate rejects invalid email."""
        with pytest.raises(ValidationError):
            schemas.PermissionCreate(user_email="not-an-email", role="viewer")


class TestLayoutSchemas:
    """Tests for layout persistence schemas."""

    def test_node_layout(self):
        """NodeLayout with position."""
        layout = schemas.NodeLayout(x=100.0, y=200.0)
        assert layout.x == 100.0
        assert layout.y == 200.0
        assert layout.label is None

    def test_node_layout_with_styling(self):
        """NodeLayout with styling."""
        layout = schemas.NodeLayout(
            x=100.0,
            y=200.0,
            label="Custom Label",
            color="#ff0000",
            metadata={"icon": "router"},
        )
        assert layout.label == "Custom Label"
        assert layout.color == "#ff0000"

    def test_annotation_layout(self):
        """AnnotationLayout for various types."""
        text = schemas.AnnotationLayout(
            id="ann-1",
            type="text",
            x=50.0,
            y=50.0,
            text="Network Core",
        )
        assert text.type == "text"
        assert text.text == "Network Core"

        rect = schemas.AnnotationLayout(
            id="ann-2",
            type="rect",
            x=0.0,
            y=0.0,
            width=200.0,
            height=100.0,
        )
        assert rect.type == "rect"
        assert rect.width == 200.0


class TestTokenSchemas:
    """Tests for authentication token schemas."""

    def test_token_out(self):
        """TokenOut with default token type."""
        token = schemas.TokenOut(access_token="jwt-token-here")
        assert token.access_token == "jwt-token-here"
        assert token.token_type == "bearer"

    def test_token_out_custom_type(self):
        """TokenOut with custom token type."""
        token = schemas.TokenOut(access_token="jwt-token", token_type="custom")
        assert token.token_type == "custom"


class TestYamlSchemas:
    """Tests for YAML content schemas."""

    def test_lab_yaml_in(self):
        """LabYamlIn with content."""
        yaml_in = schemas.LabYamlIn(content="name: test\nnodes: []")
        assert "name: test" in yaml_in.content

    def test_lab_yaml_out(self):
        """LabYamlOut with content."""
        yaml_out = schemas.LabYamlOut(content="name: test\nnodes: []")
        assert "name: test" in yaml_out.content
