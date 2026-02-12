"""Tests for labs router endpoints."""
from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


class TestLabsCRUD:
    """Tests for lab CRUD operations."""

    def test_list_labs_empty(
        self, test_client: TestClient, test_user: models.User, auth_headers: dict
    ):
        """Test listing labs when user has none."""
        response = test_client.get("/labs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "labs" in data
        assert data["labs"] == []

    def test_list_labs_with_owned_labs(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Test listing labs returns owned labs."""
        # Create labs owned by test user
        lab1 = models.Lab(name="Lab 1", owner_id=test_user.id, provider="docker")
        lab2 = models.Lab(name="Lab 2", owner_id=test_user.id, provider="docker")
        test_db.add_all([lab1, lab2])
        test_db.commit()

        response = test_client.get("/labs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["labs"]) == 2
        lab_names = {lab["name"] for lab in data["labs"]}
        assert lab_names == {"Lab 1", "Lab 2"}

    def test_list_labs_with_shared_labs(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test listing labs includes shared labs."""
        # Create lab owned by admin
        lab = models.Lab(name="Admin Lab", owner_id=admin_user.id, provider="docker")
        test_db.add(lab)
        test_db.flush()

        # Share with test user
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        response = test_client.get("/labs", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["labs"]) == 1
        assert data["labs"][0]["name"] == "Admin Lab"

    def test_list_labs_pagination(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        auth_headers: dict,
    ):
        """Test lab listing pagination."""
        # Create multiple labs
        for i in range(10):
            lab = models.Lab(
                name=f"Lab {i}", owner_id=test_user.id, provider="docker"
            )
            test_db.add(lab)
        test_db.commit()

        # Test skip and limit
        response = test_client.get("/labs?skip=2&limit=3", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["labs"]) == 3

    def test_list_labs_unauthenticated(self, test_client: TestClient):
        """Test listing labs without authentication fails."""
        response = test_client.get("/labs")
        assert response.status_code == 401

    def test_create_lab(
        self,
        test_client: TestClient,
        test_user: models.User,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test creating a new lab."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        response = test_client.post(
            "/labs",
            json={"name": "New Lab", "provider": "docker"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Lab"
        assert data["provider"] == "docker"
        assert data["owner_id"] == test_user.id
        assert data["state"] == "stopped"

    def test_create_lab_default_provider(
        self, test_client: TestClient, auth_headers: dict, tmp_path, monkeypatch
    ):
        """Test creating lab uses default provider."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        response = test_client.post(
            "/labs", json={"name": "Default Provider Lab"}, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "docker"

    def test_get_lab(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test getting a specific lab."""
        response = test_client.get(f"/labs/{sample_lab.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_lab.id
        assert data["name"] == sample_lab.name

    def test_get_lab_not_found(self, test_client: TestClient, auth_headers: dict):
        """Test getting a non-existent lab returns 404."""
        response = test_client.get("/labs/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404

    def test_get_lab_forbidden(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test getting a lab owned by another user without permission fails."""
        # Create lab owned by admin (not shared with test_user)
        lab = models.Lab(name="Private Lab", owner_id=admin_user.id, provider="docker")
        test_db.add(lab)
        test_db.commit()

        response = test_client.get(f"/labs/{lab.id}", headers=auth_headers)
        assert response.status_code == 403  # Access denied for unauthorized users

    def test_update_lab(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test updating a lab."""
        response = test_client.put(
            f"/labs/{sample_lab.id}",
            json={"name": "Updated Lab Name"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Lab Name"

    def test_update_lab_forbidden(
        self,
        test_client: TestClient,
        test_db: Session,
        test_user: models.User,
        admin_user: models.User,
        auth_headers: dict,
    ):
        """Test updating a lab owned by another user fails."""
        # Create lab owned by admin
        lab = models.Lab(name="Admin Lab", owner_id=admin_user.id, provider="docker")
        test_db.add(lab)
        test_db.flush()

        # Share with test user as viewer (not owner)
        permission = models.Permission(
            lab_id=lab.id, user_id=test_user.id, role="viewer"
        )
        test_db.add(permission)
        test_db.commit()

        response = test_client.put(
            f"/labs/{lab.id}",
            json={"name": "Unauthorized Update"},
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_delete_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test deleting a lab."""
        lab_id = sample_lab.id
        response = test_client.delete(f"/labs/{lab_id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        # Verify lab is deleted
        deleted_lab = test_db.query(models.Lab).filter(models.Lab.id == lab_id).first()
        assert deleted_lab is None


class TestLabReadiness:
    """Tests for lab readiness endpoints."""

    @patch("app.routers.labs.agent_client.check_node_readiness", new_callable=AsyncMock)
    @patch("app.routers.labs.get_online_agent_for_lab", new_callable=AsyncMock)
    def test_nodes_ready_passes_kind_for_vm_nodes(
        self,
        mock_get_online_agent_for_lab: AsyncMock,
        mock_check_node_readiness: AsyncMock,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list[models.Node],
        auth_headers: dict,
    ):
        """Ensure VM readiness checks include device kind (required for libvirt)."""
        sample_lab.provider = "libvirt"
        sample_node_definitions[0].device = "cisco_iosv"
        sample_node_definitions[0].container_name = "cisco_iosv_6"

        state = models.NodeState(
            lab_id=sample_lab.id,
            node_id=sample_node_definitions[0].gui_id,
            node_name="cisco_iosv_6",
            node_definition_id=sample_node_definitions[0].id,
            desired_state="running",
            actual_state="running",
            is_ready=False,
        )
        test_db.add(state)
        test_db.commit()

        mock_get_online_agent_for_lab.return_value = sample_host
        mock_check_node_readiness.return_value = {
            "is_ready": True,
            "progress_percent": 100,
            "message": "ready",
        }

        response = test_client.get(
            f"/labs/{sample_lab.id}/nodes/ready",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["all_ready"] is True

        assert mock_check_node_readiness.await_count >= 1
        _, kwargs = mock_check_node_readiness.await_args
        assert kwargs.get("kind") == "cisco_iosv"


class TestLabClone:
    """Tests for lab clone operations."""

    def test_clone_lab(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test cloning a lab."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        response = test_client.post(
            f"/labs/{sample_lab.id}/clone", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == f"{sample_lab.name} (copy)"
        assert data["id"] != sample_lab.id


class TestTopologyImportExport:
    """Tests for topology import/export operations."""

    def test_update_topology_from_yaml(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test updating topology from YAML."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        yaml_content = """
nodes:
  r1:
    device: linux
  r2:
    device: linux
links:
  - r1: {}
    r2: {}
"""
        response = test_client.post(
            f"/labs/{sample_lab.id}/update-topology-from-yaml",
            json={"content": yaml_content},
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_export_yaml(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test exporting YAML topology."""
        # Populate topology in database (source of truth)
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="r1",
            container_name="r1",
            device="linux",
        )
        test_db.add(node)
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/export-yaml", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "r1" in data["content"]

    def test_export_yaml_not_found(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test exporting YAML when topology doesn't exist."""
        # No nodes in database means no topology to export
        response = test_client.get(
            f"/labs/{sample_lab.id}/export-yaml", headers=auth_headers
        )
        assert response.status_code == 404

    def test_update_topology(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test syncing topology creates node states."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        graph = {
            "nodes": [
                {"id": "node-1", "name": "R1", "device": "linux"},
                {"id": "node-2", "name": "R2", "device": "linux"},
            ],
            "links": [
                {
                    "endpoints": [
                        {"node": "R1", "ifname": "eth0"},
                        {"node": "R2", "ifname": "eth0"},
                    ]
                }
            ],
        }

        response = test_client.post(
            f"/labs/{sample_lab.id}/update-topology",
            json=graph,
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify node states were created
        node_states = (
            test_db.query(models.NodeState)
            .filter(models.NodeState.lab_id == sample_lab.id)
            .all()
        )
        assert len(node_states) == 2

        # Verify link states were created
        link_states = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .all()
        )
        assert len(link_states) == 1

    def test_update_topology_preserves_container_names(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test that update-topology uses container_name when available."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(
            labs_router, "lab_workspace", lambda lab_id: tmp_path / lab_id
        )

        graph = {
            "nodes": [
                {
                    "id": "node-1",
                    "name": "Display Name",
                    "container_name": "archetype-lab1-router1",
                    "device": "linux",
                },
            ],
            "links": [],
        }

        response = test_client.post(
            f"/labs/{sample_lab.id}/update-topology",
            json=graph,
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify node state uses container_name
        node_state = (
            test_db.query(models.NodeState)
            .filter(
                models.NodeState.lab_id == sample_lab.id,
                models.NodeState.node_id == "node-1",
            )
            .first()
        )
        assert node_state is not None
        assert node_state.node_name == "archetype-lab1-router1"

    def test_export_graph(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test exporting graph topology."""
        # Populate topology in database (source of truth)
        node1 = models.Node(
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="r1",
            container_name="r1",
            device="linux",
        )
        node2 = models.Node(
            lab_id=sample_lab.id,
            gui_id="n2",
            display_name="r2",
            container_name="r2",
            device="linux",
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/export-graph", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "links" in data
        assert len(data["nodes"]) == 2

    def test_download_bundle_includes_topology_and_configs(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab: models.Lab,
        auth_headers: dict,
    ):
        """Test full bundle export includes topology, layout, configs, and orphaned configs."""
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="r1",
            container_name="r1",
            device="linux",
        )
        orphaned_node = "old-r2"
        active_snapshot = models.ConfigSnapshot(
            lab_id=sample_lab.id,
            node_name="r1",
            content="hostname r1",
            content_hash="h1",
            snapshot_type="manual",
        )
        orphaned_snapshot = models.ConfigSnapshot(
            lab_id=sample_lab.id,
            node_name=orphaned_node,
            content="hostname old-r2",
            content_hash="h2",
            snapshot_type="manual",
        )
        test_db.add_all([node, active_snapshot, orphaned_snapshot])
        test_db.commit()

        response = test_client.get(
            f"/labs/{sample_lab.id}/download-bundle", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "attachment; filename=" in response.headers["content-disposition"]

        zf = zipfile.ZipFile(io.BytesIO(response.content))
        names = set(zf.namelist())

        assert "topology/topology.yaml" in names
        assert "topology/topology.json" in names
        assert "topology/layout.json" in names
        assert "bundle-metadata.json" in names

        active_file = next(
            n for n in names if n.startswith("configs/r1/") and n.endswith("_startup-config")
        )
        orphaned_file = next(
            n
            for n in names
            if n.startswith("orphaned configs/old-r2/") and n.endswith("_startup-config")
        )
        assert zf.read(active_file).decode() == "hostname r1"
        assert zf.read(orphaned_file).decode() == "hostname old-r2"

        metadata = json.loads(zf.read("bundle-metadata.json").decode())
        assert metadata["snapshot_count"] == 2
        assert metadata["configs_count"] == 1
        assert metadata["orphaned_configs_count"] == 1


class TestNodeStates:
    """Tests for node state management."""

    def test_list_node_states(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
    ):
        """Test listing node states for a lab."""
        lab, nodes = sample_lab_with_nodes
        response = test_client.get(
            f"/labs/{lab.id}/nodes/states", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert len(data["nodes"]) == 2

    def test_get_node_state(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
    ):
        """Test getting a specific node state."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        response = test_client.get(
            f"/labs/{lab.id}/nodes/{node.node_id}/state", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == node.node_id
        assert data["node_name"] == node.node_name

    def test_set_node_desired_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
        monkeypatch,
    ):
        """Test setting a node's desired state."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(labs_router, "has_conflicting_job", lambda *a, **kw: (False, None))

        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        response = test_client.put(
            f"/labs/{lab.id}/nodes/{node.node_id}/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["desired_state"] == "running"

        # Verify in database
        test_db.refresh(node)
        assert node.desired_state == "running"

    def test_set_all_nodes_desired_state(
        self,
        test_client: TestClient,
        test_db: Session,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
        monkeypatch,
    ):
        """Test setting all nodes' desired state."""
        from app.routers import labs as labs_router

        monkeypatch.setattr(labs_router, "has_conflicting_job", lambda *a, **kw: (False, None))

        lab, nodes = sample_lab_with_nodes
        response = test_client.put(
            f"/labs/{lab.id}/nodes/desired-state",
            json={"state": "running"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 2
        for node_data in data["nodes"]:
            assert node_data["desired_state"] == "running"

    def test_set_node_desired_state_invalid(
        self,
        test_client: TestClient,
        sample_lab_with_nodes: tuple[models.Lab, list[models.NodeState]],
        auth_headers: dict,
    ):
        """Test setting an invalid desired state returns 422."""
        lab, nodes = sample_lab_with_nodes
        node = nodes[0]
        response = test_client.put(
            f"/labs/{lab.id}/nodes/{node.node_id}/desired-state",
            json={"state": "invalid"},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestLayout:
    """Tests for layout management."""

    def test_get_layout_not_found(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test getting layout when none exists returns 404."""
        workspace = tmp_path / sample_lab.id
        workspace.mkdir(parents=True)

        from app import storage

        monkeypatch.setattr(storage, "lab_workspace", lambda lab_id: workspace)
        monkeypatch.setattr(
            storage, "layout_path", lambda lab_id: workspace / "layout.json"
        )
        monkeypatch.setattr(storage, "read_layout", lambda lab_id: None)

        response = test_client.get(
            f"/labs/{sample_lab.id}/layout", headers=auth_headers
        )
        assert response.status_code == 404

    def test_save_and_get_layout(
        self,
        test_client: TestClient,
        sample_lab: models.Lab,
        auth_headers: dict,
        tmp_path,
        monkeypatch,
    ):
        """Test saving and retrieving layout."""
        workspace = tmp_path / sample_lab.id
        workspace.mkdir(parents=True)
        layout_file = workspace / "layout.json"

        from app import storage

        monkeypatch.setattr(storage, "lab_workspace", lambda lab_id: workspace)
        monkeypatch.setattr(storage, "layout_path", lambda lab_id: layout_file)

        saved_layout = None

        def mock_write_layout(lab_id, layout):
            nonlocal saved_layout
            saved_layout = layout
            layout_file.write_text(layout.model_dump_json())

        def mock_read_layout(lab_id):
            nonlocal saved_layout
            return saved_layout

        monkeypatch.setattr(storage, "write_layout", mock_write_layout)
        monkeypatch.setattr(storage, "read_layout", mock_read_layout)

        layout_data = {
            "version": 1,
            "canvas": {"zoom": 1.0, "offsetX": 0, "offsetY": 0},
            "nodes": {"r1": {"x": 100, "y": 200}},
            "annotations": [],
        }

        # Save layout
        response = test_client.put(
            f"/labs/{sample_lab.id}/layout",
            json=layout_data,
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Get layout
        response = test_client.get(
            f"/labs/{sample_lab.id}/layout", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 1
        assert "r1" in data["nodes"]
        assert data["nodes"]["r1"]["x"] == 100
