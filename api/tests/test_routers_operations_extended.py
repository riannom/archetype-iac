"""Extended tests for api/app/routers/labs/operations.py endpoints.

Covers:
- GET  /labs/{lab_id}/nodes/ready
- GET  /labs/{lab_id}/inventory
- GET  /labs/{lab_id}/logs
- GET  /labs/{lab_id}/nodes/{node_id}/interfaces
- POST /labs/{lab_id}/interface-mappings/sync
- GET  /labs/{lab_id}/infra/notifications
- POST /labs/{lab_id}/cleanup-orphans
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node_state(
    db: Session,
    lab_id: str,
    node_id: str,
    node_name: str,
    desired: str = "stopped",
    actual: str = "undeployed",
    is_ready: bool = False,
    management_ip: str | None = None,
    management_ips_json: str | None = None,
    error_message: str | None = None,
) -> models.NodeState:
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        is_ready=is_ready,
        management_ip=management_ip,
        management_ips_json=management_ips_json,
        error_message=error_message,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


def _make_node(
    db: Session,
    lab_id: str,
    node_id: str,
    gui_id: str,
    display_name: str,
    container_name: str,
    device: str = "linux",
    host_id: str | None = None,
    image: str | None = None,
) -> models.Node:
    n = models.Node(
        id=node_id,
        lab_id=lab_id,
        gui_id=gui_id,
        display_name=display_name,
        container_name=container_name,
        device=device,
        host_id=host_id,
        image=image,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _make_link_state(
    db: Session,
    link_id: str,
    lab_id: str,
    link_name: str,
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
    desired: str = "up",
    actual: str = "up",
    error_message: str | None = None,
) -> models.LinkState:
    ls = models.LinkState(
        id=link_id,
        lab_id=lab_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        desired_state=desired,
        actual_state=actual,
        error_message=error_message,
    )
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return ls


def _make_vxlan_tunnel(
    db: Session,
    tunnel_id: str,
    lab_id: str,
    link_state_id: str,
    vni: int,
    status: str = "active",
    error_message: str | None = None,
    agent_a_id: str = "a1",
    agent_b_id: str = "a2",
) -> models.VxlanTunnel:
    t = models.VxlanTunnel(
        id=tunnel_id,
        lab_id=lab_id,
        link_state_id=link_state_id,
        vni=vni,
        vlan_tag=200,
        agent_a_id=agent_a_id,
        agent_a_ip="10.0.0.1",
        agent_b_id=agent_b_id,
        agent_b_ip="10.0.0.2",
        status=status,
        error_message=error_message,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ============================================================================
# GET /labs/{lab_id}/nodes/ready
# ============================================================================


class TestCheckNodesReady:
    """Tests for the readiness endpoint."""

    def test_ready_no_nodes(self, test_client, auth_headers, sample_lab, monkeypatch):
        """Empty lab returns all_ready=True with zero counts."""
        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=False)
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)
        monkeypatch.setattr(
            "app.routers.labs.get_online_agent_for_lab",
            AsyncMock(return_value=None),
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/ready", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ready"] is True
        assert data["ready_count"] == 0
        assert data["running_count"] == 0

    def test_ready_running_node_already_ready(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """Running + is_ready node is counted."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", is_ready=True,
        )
        _make_node(test_db, sample_lab.id, "nd1", "n1", "R1", "R1", device="linux")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=False)
        mock_ac.check_node_readiness = AsyncMock(return_value={"is_ready": True})
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)
        monkeypatch.setattr(
            "app.routers.labs.get_online_agent_for_lab",
            AsyncMock(return_value=None),
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/ready", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["all_ready"] is True
        assert data["ready_count"] == 1
        assert data["running_count"] == 1
        assert data["nodes"][0]["is_ready"] is True

    def test_ready_running_not_ready_agent_check(
        self, test_client, auth_headers, sample_lab, test_db, sample_host, monkeypatch
    ):
        """Agent readiness check updates node to ready."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", is_ready=False,
        )
        _make_node(test_db, sample_lab.id, "nd1", "n1", "R1", "R1", device="linux")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.check_node_readiness = AsyncMock(
            return_value={"is_ready": True, "progress_percent": 100, "message": "Boot complete"}
        )
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)
        monkeypatch.setattr(
            "app.routers.labs.get_online_agent_for_lab",
            AsyncMock(return_value=sample_host),
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/ready", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready_count"] == 1
        assert data["nodes"][0]["progress_percent"] == 100

    def test_ready_agent_check_failure(
        self, test_client, auth_headers, sample_lab, test_db, sample_host, monkeypatch
    ):
        """Agent readiness check exception is caught gracefully."""
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", is_ready=False,
        )
        _make_node(test_db, sample_lab.id, "nd1", "n1", "R1", "R1")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.check_node_readiness = AsyncMock(side_effect=Exception("timeout"))
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)
        monkeypatch.setattr(
            "app.routers.labs.get_online_agent_for_lab",
            AsyncMock(return_value=sample_host),
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/ready", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "timeout" in data["nodes"][0]["message"]
        assert data["nodes"][0]["is_ready"] is False

    def test_ready_404_nonexistent_lab(self, test_client, auth_headers):
        """Non-existent lab returns 404."""
        resp = test_client.get("/labs/no-such-lab/nodes/ready", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================================
# GET /labs/{lab_id}/inventory
# ============================================================================


class TestExportInventory:
    """Tests for the inventory export endpoint."""

    def test_inventory_json_format(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """JSON inventory returns node list."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", management_ip="10.0.0.1",
            management_ips_json=json.dumps(["10.0.0.1", "fd00::1"]),
        )

        mock_topo = MagicMock()
        mock_topo_inst = mock_topo.return_value
        mock_topo_inst.has_nodes.return_value = False
        monkeypatch.setattr("app.routers.labs.TopologyService", mock_topo)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=json", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "json"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["management_ip"] == "10.0.0.1"
        assert data["nodes"][0]["all_ips"] == ["10.0.0.1", "fd00::1"]
        assert data["content"] is None  # JSON format has no formatted content

    def test_inventory_ansible_format(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """Ansible inventory generates YAML content."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", management_ip="10.0.0.1",
        )

        mock_topo = MagicMock()
        mock_topo_inst = mock_topo.return_value
        mock_topo_inst.has_nodes.return_value = False
        monkeypatch.setattr("app.routers.labs.TopologyService", mock_topo)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=ansible", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "ansible"
        assert data["content"] is not None
        assert "ansible_host" in data["content"]
        assert "10.0.0.1" in data["content"]

    def test_inventory_terraform_format(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """Terraform inventory generates JSON content."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", management_ip="10.0.0.2",
        )

        mock_topo = MagicMock()
        mock_topo_inst = mock_topo.return_value
        mock_topo_inst.has_nodes.return_value = False
        monkeypatch.setattr("app.routers.labs.TopologyService", mock_topo)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=terraform", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "terraform"
        content = json.loads(data["content"])
        assert content["lab_id"] == sample_lab.id
        assert "R1" in content["lab_nodes"]

    def test_inventory_with_topology_device_info(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """Inventory includes device info from topology."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running",
        )

        mock_graph_node = MagicMock()
        mock_graph_node.id = "n1"
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph_node.device = "ceos"

        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        mock_topo = MagicMock()
        mock_topo_inst = mock_topo.return_value
        mock_topo_inst.has_nodes.return_value = True
        mock_topo_inst.export_to_graph.return_value = mock_graph
        monkeypatch.setattr("app.routers.labs.TopologyService", mock_topo)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=json", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"][0]["device_type"] == "ceos"

    def test_inventory_404_nonexistent_lab(self, test_client, auth_headers):
        resp = test_client.get("/labs/missing/inventory", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================================
# GET /labs/{lab_id}/logs
# ============================================================================


class TestGetLabLogs:
    """Tests for the lab logs endpoint."""

    def test_logs_no_jobs(
        self, test_client, auth_headers, sample_lab
    ):
        """Lab with no jobs returns empty logs."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/logs", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total_count"] == 0
        assert data["error_count"] == 0

    def test_logs_with_job(
        self, test_client, auth_headers, sample_lab, test_db, test_user, monkeypatch
    ):
        """Job with log content returns parsed entries."""
        job = models.Job(
            id="log-job-1",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
            log_path="[INFO] Deploy started\n[ERROR] Something failed",
        )
        test_db.add(job)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/logs", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] >= 1

    def test_logs_404_nonexistent_lab(self, test_client, auth_headers):
        resp = test_client.get("/labs/missing/logs", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================================
# GET /labs/{lab_id}/nodes/{node_id}/interfaces
# ============================================================================


class TestGetNodeInterfaces:
    """Tests for the per-node interface mappings endpoint."""

    def test_interfaces_returns_mappings(
        self, test_client, auth_headers, sample_lab, test_db
    ):
        """Returns interface mappings for a specific node."""
        node = _make_node(
            test_db, sample_lab.id, "nd1", "n1", "R1", "R1", device="ceos"
        )
        mapping = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=node.id,
            linux_interface="eth1",
            ovs_port="vh1234",
            ovs_bridge="arch-ovs",
            vlan_tag=100,
            vendor_interface="Ethernet1",
            device_type="ceos",
        )
        test_db.add(mapping)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/n1/interfaces", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["mappings"][0]["linux_interface"] == "eth1"
        assert data["mappings"][0]["vendor_interface"] == "Ethernet1"

    def test_interfaces_node_not_found(
        self, test_client, auth_headers, sample_lab
    ):
        """Non-existent node returns 404."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/nonexistent/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_interfaces_empty(
        self, test_client, auth_headers, sample_lab, test_db
    ):
        """Node with no mappings returns empty list."""
        _make_node(test_db, sample_lab.id, "nd1", "n1", "R1", "R1")

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/n1/interfaces", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["mappings"] == []


# ============================================================================
# POST /labs/{lab_id}/interface-mappings/sync
# ============================================================================


class TestSyncInterfaceMappings:
    """Tests for the interface mapping sync endpoint."""

    def test_sync_success(
        self, test_client, auth_headers, sample_lab, monkeypatch
    ):
        """Successful sync returns counts."""
        mock_svc = MagicMock()
        mock_svc.populate_all_agents = AsyncMock(
            return_value={"created": 3, "updated": 1, "errors": 0, "agents_queried": 2}
        )
        monkeypatch.setattr("app.routers.labs.interface_mapping_service", mock_svc)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/interface-mappings/sync", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 3
        assert data["updated"] == 1
        assert data["agents_queried"] == 2

    def test_sync_404_nonexistent_lab(self, test_client, auth_headers, monkeypatch):
        """Non-existent lab returns 404."""
        mock_svc = MagicMock()
        monkeypatch.setattr("app.routers.labs.interface_mapping_service", mock_svc)

        resp = test_client.post(
            "/labs/missing/interface-mappings/sync", headers=auth_headers
        )
        assert resp.status_code == 404


# ============================================================================
# GET /labs/{lab_id}/infra/notifications
# ============================================================================


class TestInfraNotifications:
    """Tests for the infrastructure notifications endpoint."""

    def test_no_notifications(self, test_client, auth_headers, sample_lab):
        """Lab with no issues returns empty list."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []

    def test_tunnel_cleanup_notification(
        self, test_client, auth_headers, sample_lab, test_db, multiple_hosts
    ):
        """Tunnel in cleanup status surfaces as warning."""
        ls = _make_link_state(
            test_db, "ls1", sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
        )
        _make_vxlan_tunnel(
            test_db, "t1", sample_lab.id, ls.id, vni=5001,
            status="cleanup", error_message="OVS port still exists",
            agent_a_id=multiple_hosts[0].id, agent_b_id=multiple_hosts[1].id,
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) == 1
        n = data["notifications"][0]
        assert n["severity"] == "warning"
        assert n["category"] == "tunnel_cleanup"
        assert "VNI 5001" in n["title"]

    def test_tunnel_failed_notification(
        self, test_client, auth_headers, sample_lab, test_db, multiple_hosts
    ):
        """Tunnel in failed status surfaces as error."""
        ls = _make_link_state(
            test_db, "ls2", sample_lab.id, "R1:eth2-R2:eth2",
            "R1", "eth2", "R2", "eth2",
        )
        _make_vxlan_tunnel(
            test_db, "t2", sample_lab.id, ls.id, vni=5002,
            status="failed", error_message="VXLAN creation failed",
            agent_a_id=multiple_hosts[0].id, agent_b_id=multiple_hosts[1].id,
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        data = resp.json()
        assert len(data["notifications"]) == 1
        assert data["notifications"][0]["severity"] == "error"
        assert data["notifications"][0]["category"] == "tunnel_failed"

    def test_tunnel_error_message_only(
        self, test_client, auth_headers, sample_lab, test_db, multiple_hosts
    ):
        """Tunnel with error_message but active status still shows notification."""
        ls = _make_link_state(
            test_db, "ls3", sample_lab.id, "R3:eth1-R4:eth1",
            "R3", "eth1", "R4", "eth1",
        )
        _make_vxlan_tunnel(
            test_db, "t3", sample_lab.id, ls.id, vni=5003,
            status="active", error_message="Intermittent connectivity",
            agent_a_id=multiple_hosts[0].id, agent_b_id=multiple_hosts[1].id,
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        data = resp.json()
        assert len(data["notifications"]) == 1
        assert data["notifications"][0]["category"] == "tunnel_cleanup"

    def test_link_error_notification(
        self, test_client, auth_headers, sample_lab, test_db
    ):
        """Link in error state surfaces as notification."""
        _make_link_state(
            test_db, "ls-err", sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
            desired="up", actual="error", error_message="VLAN mismatch",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        data = resp.json()
        assert any(n["category"] == "link_error" for n in data["notifications"])
        link_n = [n for n in data["notifications"] if n["category"] == "link_error"][0]
        assert link_n["severity"] == "error"
        assert "VLAN mismatch" in (link_n["detail"] or "")

    def test_node_error_notification(
        self, test_client, auth_headers, sample_lab, test_db
    ):
        """Node in error state surfaces as notification."""
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="error", error_message="Container crash",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        data = resp.json()
        assert any(n["category"] == "node_error" for n in data["notifications"])
        node_n = [n for n in data["notifications"] if n["category"] == "node_error"][0]
        assert node_n["severity"] == "error"
        assert "Container crash" in (node_n["detail"] or "")

    def test_mixed_notifications(
        self, test_client, auth_headers, sample_lab, test_db, multiple_hosts
    ):
        """Multiple notification types returned together."""
        # Error node
        _make_node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="error", error_message="OOM",
        )
        # Error link
        _make_link_state(
            test_db, "ls-m", sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
            desired="up", actual="error",
        )
        # Failed tunnel
        ls2 = _make_link_state(
            test_db, "ls-m2", sample_lab.id, "R3:eth1-R4:eth1",
            "R3", "eth1", "R4", "eth1",
        )
        _make_vxlan_tunnel(
            test_db, "t-m", sample_lab.id, ls2.id, vni=9999,
            status="failed",
            agent_a_id=multiple_hosts[0].id, agent_b_id=multiple_hosts[1].id,
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications", headers=auth_headers
        )
        data = resp.json()
        categories = {n["category"] for n in data["notifications"]}
        assert "node_error" in categories
        assert "link_error" in categories
        assert "tunnel_failed" in categories

    def test_notifications_404_nonexistent_lab(self, test_client, auth_headers):
        resp = test_client.get("/labs/missing/infra/notifications", headers=auth_headers)
        assert resp.status_code == 404


# ============================================================================
# POST /labs/{lab_id}/cleanup-orphans
# ============================================================================


class TestCleanupOrphans:
    """Tests for the orphan cleanup endpoint."""

    def test_cleanup_success(
        self, test_client, auth_headers, sample_lab, test_db, sample_host, monkeypatch
    ):
        """Successful cleanup returns removed containers."""
        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(
            return_value={"removed_containers": ["archetype-old-r3"], "errors": []}
        )
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Test Agent" in data["removed_by_agent"]
        assert data["removed_by_agent"]["Test Agent"] == ["archetype-old-r3"]

    def test_cleanup_agent_error(
        self, test_client, auth_headers, sample_lab, test_db, sample_host, monkeypatch
    ):
        """Agent communication error is returned in errors list."""
        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(side_effect=Exception("Connection refused"))
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) == 1
        assert "Connection refused" in data["errors"][0]

    def test_cleanup_no_online_agents(
        self, test_client, auth_headers, sample_lab, test_db, monkeypatch
    ):
        """No online agents means nothing to clean."""
        mock_ac = MagicMock()
        monkeypatch.setattr("app.routers.labs.agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed_by_agent"] == {}
        assert data["errors"] == []

    def test_cleanup_404_nonexistent_lab(self, test_client, auth_headers):
        resp = test_client.post("/labs/missing/cleanup-orphans", headers=auth_headers)
        assert resp.status_code == 404
