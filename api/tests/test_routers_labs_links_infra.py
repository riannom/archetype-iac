"""Tests for link state management, hot-connect/disconnect, config diff,
logs, cleanup, interface mappings, notifications, inventory, and link
reconciliation endpoints in api/app/routers/labs.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_running_lab(
    test_db: Session, owner_id: str, *, name: str = "Running Lab"
) -> models.Lab:
    lab = models.Lab(
        name=name,
        owner_id=owner_id,
        provider="docker",
        state="running",
        workspace_path="/tmp/running-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_link_state(
    test_db: Session,
    lab_id: str,
    *,
    link_name: str = "R1:eth1-R2:eth1",
    source_node: str = "R1",
    source_interface: str = "eth1",
    target_node: str = "R2",
    target_interface: str = "eth1",
    desired_state: str = "up",
    actual_state: str = "unknown",
    is_cross_host: bool = False,
    source_host_id: str | None = None,
    target_host_id: str | None = None,
    error_message: str | None = None,
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        desired_state=desired_state,
        actual_state=actual_state,
        is_cross_host=is_cross_host,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        error_message=error_message,
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


def _make_node(
    test_db: Session,
    lab_id: str,
    *,
    gui_id: str = "n1",
    display_name: str = "R1",
    container_name: str = "R1",
    device: str = "linux",
    host_id: str | None = None,
) -> models.Node:
    node = models.Node(
        lab_id=lab_id,
        gui_id=gui_id,
        display_name=display_name,
        container_name=container_name,
        device=device,
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_node_state(
    test_db: Session,
    lab_id: str,
    *,
    node_id: str = "n1",
    node_name: str = "R1",
    desired_state: str = "running",
    actual_state: str = "running",
    management_ip: str | None = None,
    management_ips_json: str | None = None,
    error_message: str | None = None,
) -> models.NodeState:
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired_state,
        actual_state=actual_state,
        management_ip=management_ip,
        management_ips_json=management_ips_json,
        error_message=error_message,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


def _make_config_snapshot(
    test_db: Session,
    lab_id: str,
    *,
    node_name: str = "R1",
    content: str = "hostname R1\n",
    snapshot_type: str = "manual",
) -> models.ConfigSnapshot:
    import hashlib
    snap = models.ConfigSnapshot(
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        snapshot_type=snapshot_type,
    )
    test_db.add(snap)
    test_db.commit()
    test_db.refresh(snap)
    return snap


# ===========================================================================
# TestCheckResources
# ===========================================================================


class TestCheckResources:
    """POST /labs/{id}/check-resources"""

    def test_success_with_available_resources(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list,
    ):
        """Returns sufficient=True when agents have capacity."""
        mock_result = MagicMock()
        mock_result.fits = True
        mock_result.has_warnings = False
        mock_result.agent_name = "Test Agent"
        mock_result.projected_memory_pct = 50.0
        mock_result.projected_cpu_pct = 30.0
        mock_result.projected_disk_pct = 40.0
        mock_result.node_count = 2
        mock_result.required_memory_mb = 1024
        mock_result.required_cpu_cores = 2
        mock_result.available_memory_mb = 8192
        mock_result.available_cpu_cores = 8
        mock_result.errors = []
        mock_result.warnings = []

        with patch(
            "app.routers.labs.TopologyService"
        ) as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.get_nodes.return_value = sample_node_definitions
            with patch(
                "app.services.resource_capacity.check_multihost_capacity",
                return_value={sample_host.id: mock_result},
            ):
                resp = test_client.post(
                    f"/labs/{sample_lab.id}/check-resources",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sufficient"] is True

    def test_insufficient_resources_warning(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
        sample_node_definitions: list,
    ):
        """Returns sufficient=False with errors when resources are insufficient."""
        mock_result = MagicMock()
        mock_result.fits = False
        mock_result.has_warnings = True
        mock_result.agent_name = "Test Agent"
        mock_result.projected_memory_pct = 95.0
        mock_result.projected_cpu_pct = 80.0
        mock_result.projected_disk_pct = 90.0
        mock_result.node_count = 2
        mock_result.required_memory_mb = 16384
        mock_result.required_cpu_cores = 8
        mock_result.available_memory_mb = 4096
        mock_result.available_cpu_cores = 4
        mock_result.errors = ["Insufficient memory"]
        mock_result.warnings = ["High CPU usage"]

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.get_nodes.return_value = sample_node_definitions
            with patch(
                "app.services.resource_capacity.check_multihost_capacity",
                return_value={sample_host.id: mock_result},
            ):
                resp = test_client.post(
                    f"/labs/{sample_lab.id}/check-resources",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sufficient"] is False
        assert len(data["errors"]) > 0

    def test_no_agents_empty_response(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns empty per_host when no nodes/agents are mapped."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.get_nodes.return_value = []
            resp = test_client.post(
                f"/labs/{sample_lab.id}/check-resources",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sufficient"] is True
        assert data["per_host"] == {}


# ===========================================================================
# TestListLinkStates
# ===========================================================================


class TestListLinkStates:
    """GET /labs/{id}/links/states"""

    def test_empty_no_links(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns empty links list when no links exist."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/states", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["links"] == []

    def test_multiple_link_states(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns all link states for a lab."""
        _make_link_state(test_db, sample_lab.id, link_name="R1:eth1-R2:eth1")
        _make_link_state(
            test_db,
            sample_lab.id,
            link_name="R2:eth2-R3:eth1",
            source_node="R2",
            source_interface="eth2",
            target_node="R3",
            target_interface="eth1",
        )

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/states", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["links"]) == 2

    def test_lab_not_found(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Returns 403/404 for nonexistent lab."""
        resp = test_client.get(
            "/labs/nonexistent/links/states", headers=auth_headers
        )
        assert resp.status_code in (403, 404)


# ===========================================================================
# TestGetLinkState
# ===========================================================================


class TestGetLinkState:
    """GET /labs/{id}/links/{link_name}/state"""

    def test_existing_link_state(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns state for an existing link."""
        ls = _make_link_state(test_db, sample_lab.id)
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/{ls.link_name}/state",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["link_name"] == ls.link_name

    def test_link_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 404 for nonexistent link."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/nonexistent/state",
                headers=auth_headers,
            )
        assert resp.status_code == 404


# ===========================================================================
# TestGetLinkDetail
# ===========================================================================


class TestGetLinkDetail:
    """GET /labs/{id}/links/{link_name}/detail"""

    def test_same_host_link(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Returns detail for a same-host link."""
        ls = _make_link_state(
            test_db,
            sample_lab.id,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/{ls.link_name}/detail",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["link_name"] == ls.link_name
        assert data["is_cross_host"] is False
        assert data["tunnel"] is None

    def test_cross_host_with_tunnel(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        multiple_hosts: list[models.Host],
    ):
        """Returns detail with tunnel info for cross-host link."""
        ls = _make_link_state(
            test_db,
            sample_lab.id,
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=ls.id,
            vni=10001,
            vlan_tag=200,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="10.0.0.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/{ls.link_name}/detail",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_cross_host"] is True
        assert data["tunnel"] is not None
        assert data["tunnel"]["vni"] == 10001

    def test_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 404 for nonexistent link detail."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/links/nonexistent/detail",
                headers=auth_headers,
            )
        assert resp.status_code == 404


# ===========================================================================
# TestSetLinkState
# ===========================================================================


class TestSetLinkState:
    """PUT /labs/{id}/links/{link_name}/state"""

    def test_set_link_up(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Sets desired state to up."""
        ls = _make_link_state(test_db, sample_lab.id, desired_state="down")
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            with patch(
                "app.routers.labs.sync_link_endpoint_reservations",
                return_value=(True, []),
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.put(
                        f"/labs/{sample_lab.id}/links/{ls.link_name}/state",
                        json={"state": "up"},
                        headers=auth_headers,
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_state"] == "up"

    def test_set_link_down(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Sets desired state to down."""
        ls = _make_link_state(test_db, sample_lab.id, desired_state="up")
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            with patch(
                "app.routers.labs.sync_link_endpoint_reservations",
                return_value=(True, []),
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.put(
                        f"/labs/{sample_lab.id}/links/{ls.link_name}/state",
                        json={"state": "down"},
                        headers=auth_headers,
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_state"] == "down"

    def test_link_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 404 when link does not exist."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.put(
                f"/labs/{sample_lab.id}/links/nonexistent/state",
                json={"state": "up"},
                headers=auth_headers,
            )
        assert resp.status_code == 404


# ===========================================================================
# TestSetAllLinksDesiredState
# ===========================================================================


class TestSetAllLinksDesiredState:
    """PUT /labs/{id}/links/desired-state"""

    def test_set_all_links_up(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Sets all links desired state to up."""
        _make_link_state(test_db, sample_lab.id, desired_state="down")
        _make_link_state(
            test_db,
            sample_lab.id,
            link_name="R2:eth2-R3:eth1",
            source_node="R2",
            source_interface="eth2",
            target_node="R3",
            target_interface="eth1",
            desired_state="down",
        )
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            with patch(
                "app.routers.labs.sync_link_endpoint_reservations",
                return_value=(True, []),
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.put(
                        f"/labs/{sample_lab.id}/links/desired-state",
                        json={"state": "up"},
                        headers=auth_headers,
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert all(link["desired_state"] == "up" for link in data["links"])

    def test_set_all_links_down(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Sets all links desired state to down."""
        _make_link_state(test_db, sample_lab.id, desired_state="up")
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            with patch(
                "app.routers.labs.sync_link_endpoint_reservations",
                return_value=(True, []),
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.put(
                        f"/labs/{sample_lab.id}/links/desired-state",
                        json={"state": "down"},
                        headers=auth_headers,
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert all(link["desired_state"] == "down" for link in data["links"])


# ===========================================================================
# TestRefreshLinkStates
# ===========================================================================


class TestRefreshLinkStates:
    """POST /labs/{id}/links/refresh"""

    def test_successful_refresh(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Refreshes link states from topology."""
        mock_graph = MagicMock()
        mock_graph.links = []
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = True
            mock_svc.export_to_graph.return_value = mock_graph
            with patch(
                "app.routers.labs._upsert_link_states",
                return_value=(1, 0, [], []),
            ):
                resp = test_client.post(
                    f"/labs/{sample_lab.id}/links/refresh",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Link states refreshed"
        assert data["links_created"] == 1

    def test_no_topology(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 404 when topology does not exist."""
        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.post(
                f"/labs/{sample_lab.id}/links/refresh",
                headers=auth_headers,
            )
        assert resp.status_code == 404


# ===========================================================================
# TestHotConnectLink
# ===========================================================================


class TestHotConnectLink:
    """POST /labs/{id}/hot-connect"""

    def test_successful_hot_connect(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Successfully hot-connects two interfaces."""
        lab = _make_running_lab(test_db, test_user.id)
        _make_node(test_db, lab.id, gui_id="n1", container_name="R1", host_id=sample_host.id)
        _make_node(test_db, lab.id, gui_id="n2", display_name="R2", container_name="R2", host_id=sample_host.id)

        with patch(
            "app.routers.labs._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={sample_host.id: sample_host},
        ):
            with patch(
                "app.routers.labs.create_link_if_ready",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.post(
                        f"/labs/{lab.id}/hot-connect",
                        json={
                            "source_node": "R1",
                            "source_interface": "eth1",
                            "target_node": "R2",
                            "target_interface": "eth1",
                        },
                        headers=auth_headers,
                    )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_lab_not_running(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 400 when lab is not running."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/hot-connect",
            json={
                "source_node": "R1",
                "source_interface": "eth1",
                "target_node": "R2",
                "target_interface": "eth1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "running" in resp.json()["detail"].lower()

    def test_creates_link_state_record(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Hot-connect creates a LinkState record if none exists."""
        lab = _make_running_lab(test_db, test_user.id)
        _make_node(test_db, lab.id, gui_id="n1", container_name="R1", host_id=sample_host.id)
        _make_node(test_db, lab.id, gui_id="n2", display_name="R2", container_name="R2", host_id=sample_host.id)

        # Verify no link states before
        count_before = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab.id)
            .count()
        )
        assert count_before == 0

        with patch(
            "app.routers.labs._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={sample_host.id: sample_host},
        ):
            with patch(
                "app.routers.labs.create_link_if_ready",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch("app.routers.labs.recompute_link_oper_state"):
                    resp = test_client.post(
                        f"/labs/{lab.id}/hot-connect",
                        json={
                            "source_node": "R1",
                            "source_interface": "eth1",
                            "target_node": "R2",
                            "target_interface": "eth1",
                        },
                        headers=auth_headers,
                    )
        assert resp.status_code == 200

        # Verify link state was created
        count_after = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab.id)
            .count()
        )
        assert count_after >= 1


# ===========================================================================
# TestHotDisconnectLink
# ===========================================================================


class TestHotDisconnectLink:
    """DELETE /labs/{id}/hot-disconnect/{link_id}"""

    def test_successful_disconnect(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Successfully disconnects a link."""
        lab = _make_running_lab(test_db, test_user.id)
        ls = _make_link_state(test_db, lab.id, actual_state="up")

        with patch(
            "app.routers.labs._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={sample_host.id: sample_host},
        ):
            with patch(
                "app.routers.labs.teardown_link",
                new_callable=AsyncMock,
                return_value=True,
            ):
                resp = test_client.delete(
                    f"/labs/{lab.id}/hot-disconnect/{ls.link_name}",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_lab_not_running_still_attempts(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_host: models.Host,
    ):
        """Hot-disconnect attempts even for non-running labs (editor check only)."""
        ls = _make_link_state(test_db, sample_lab.id, actual_state="up")

        with patch(
            "app.routers.labs._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={sample_host.id: sample_host},
        ):
            with patch(
                "app.routers.labs.teardown_link",
                new_callable=AsyncMock,
                return_value=True,
            ):
                resp = test_client.delete(
                    f"/labs/{sample_lab.id}/hot-disconnect/{ls.link_name}",
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_link_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Returns error when link does not exist."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs._build_host_to_agent_map",
            new_callable=AsyncMock,
            return_value={sample_host.id: sample_host},
        ):
            resp = test_client.delete(
                f"/labs/{lab.id}/hot-disconnect/nonexistent:eth1-other:eth1",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()


# ===========================================================================
# TestListLiveLinks
# ===========================================================================


class TestListLiveLinks:
    """GET /labs/{id}/live-links"""

    def test_returns_live_link_info(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Returns live link info from agent."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.get_online_agent_for_lab",
            new_callable=AsyncMock,
            return_value=sample_host,
        ):
            with patch(
                "app.routers.labs.agent_client.list_links_on_agent",
                new_callable=AsyncMock,
                return_value={"links": [{"name": "R1:eth1-R2:eth1", "vlan": 100}]},
            ):
                resp = test_client.get(
                    f"/labs/{lab.id}/live-links", headers=auth_headers
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "links" in data

    def test_no_agent_available(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
    ):
        """Returns error when no agent is available."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.get_online_agent_for_lab",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = test_client.get(
                f"/labs/{lab.id}/live-links", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# ===========================================================================
# TestConnectExternalNetwork
# ===========================================================================


class TestConnectExternalNetwork:
    """POST /labs/{id}/external/connect"""

    def test_successful_external_connection(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Successfully connects to external network."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.get_online_agent_for_lab",
            new_callable=AsyncMock,
            return_value=sample_host,
        ):
            with patch(
                "app.routers.labs.agent_client.connect_external_on_agent",
                new_callable=AsyncMock,
                return_value={"success": True, "vlan_tag": 300},
            ):
                resp = test_client.post(
                    f"/labs/{lab.id}/external/connect",
                    json={
                        "node_name": "R1",
                        "interface_name": "eth2",
                        "external_interface": "ens192",
                    },
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["vlan_tag"] == 300

    def test_lab_not_running(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 400 when lab is not running."""
        resp = test_client.post(
            f"/labs/{sample_lab.id}/external/connect",
            json={
                "node_name": "R1",
                "interface_name": "eth2",
                "external_interface": "ens192",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_agent_unavailable(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
    ):
        """Returns 503 when no agent is available."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.get_online_agent_for_lab",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/external/connect",
                json={
                    "node_name": "R1",
                    "interface_name": "eth2",
                    "external_interface": "ens192",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 503


# ===========================================================================
# TestGenerateConfigDiff
# ===========================================================================


class TestGenerateConfigDiff:
    """POST /labs/{id}/config-diff"""

    def test_successful_diff_with_changes(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns diff lines between two different snapshots."""
        snap_a = _make_config_snapshot(
            test_db, sample_lab.id, content="hostname R1\ninterface eth0\n"
        )
        snap_b = _make_config_snapshot(
            test_db, sample_lab.id, content="hostname R1\ninterface eth1\n"
        )

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={
                "snapshot_id_a": snap_a.id,
                "snapshot_id_b": snap_b.id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] > 0 or data["deletions"] > 0
        assert len(data["diff_lines"]) > 0

    def test_snapshot_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns 404 when snapshot_a does not exist."""
        snap_b = _make_config_snapshot(test_db, sample_lab.id)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={
                "snapshot_id_a": "nonexistent",
                "snapshot_id_b": snap_b.id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_identical_configs(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns zero additions/deletions for identical configs."""
        content = "hostname R1\ninterface eth0\n"
        snap_a = _make_config_snapshot(test_db, sample_lab.id, content=content)
        snap_b = _make_config_snapshot(test_db, sample_lab.id, content=content)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={
                "snapshot_id_a": snap_a.id,
                "snapshot_id_b": snap_b.id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] == 0
        assert data["deletions"] == 0

    def test_missing_snapshot_b(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns 404 when snapshot_b does not exist."""
        snap_a = _make_config_snapshot(test_db, sample_lab.id)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={
                "snapshot_id_a": snap_a.id,
                "snapshot_id_b": "nonexistent",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# TestGetLabLogs
# ===========================================================================


class TestGetLabLogs:
    """GET /labs/{id}/logs"""

    def test_empty_logs(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns empty entries when no jobs exist."""
        with patch("app.utils.logs.get_log_content", return_value=None):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/logs", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total_count"] == 0

    def test_with_filter_parameters(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Accepts filter parameters without error."""
        with patch("app.utils.logs.get_log_content", return_value=None):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/logs?level=error&search=failed&since=1h",
                headers=auth_headers,
            )
        assert resp.status_code == 200

    def test_job_filter(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        test_user: models.User,
    ):
        """Filters logs by job_id."""
        job = models.Job(
            id="log-test-job",
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="completed",
        )
        test_db.add(job)
        test_db.commit()

        with patch("app.utils.logs.get_log_content", return_value=None):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/logs?job_id=log-test-job",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        # The job should appear in jobs list
        job_ids = [j["id"] for j in data["jobs"]]
        assert "log-test-job" in job_ids

    def test_limit_parameter(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Accepts limit parameter."""
        with patch("app.utils.logs.get_log_content", return_value=None):
            resp = test_client.get(
                f"/labs/{sample_lab.id}/logs?limit=10", headers=auth_headers
            )
        assert resp.status_code == 200


# ===========================================================================
# TestCleanupLabOrphans
# ===========================================================================


class TestCleanupLabOrphans:
    """POST /labs/{id}/cleanup-orphans"""

    def test_successful_cleanup(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Successfully cleans up orphaned containers."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.agent_client.cleanup_lab_orphans",
            new_callable=AsyncMock,
            return_value={"removed_containers": ["archetype-lab-old-r1"]},
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/cleanup-orphans", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["removed_by_agent"]) > 0

    def test_no_orphans_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Returns empty result when no orphans found."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.agent_client.cleanup_lab_orphans",
            new_callable=AsyncMock,
            return_value={"removed_containers": []},
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/cleanup-orphans", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed_by_agent"] == {}

    def test_agent_unavailable(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        test_user: models.User,
        sample_host: models.Host,
    ):
        """Records error when agent call fails."""
        lab = _make_running_lab(test_db, test_user.id)

        with patch(
            "app.routers.labs.agent_client.cleanup_lab_orphans",
            new_callable=AsyncMock,
            side_effect=Exception("Connection refused"),
        ):
            resp = test_client.post(
                f"/labs/{lab.id}/cleanup-orphans", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) > 0


# ===========================================================================
# TestInterfaceMappings
# ===========================================================================


class TestInterfaceMappings:
    """GET /labs/{id}/interface-mappings and GET /labs/{id}/nodes/{node_id}/interfaces"""

    def test_lab_interface_mappings(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_node_definitions: list[models.Node],
    ):
        """Returns interface mappings for the lab."""
        node = sample_node_definitions[0]
        mapping = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=node.id,
            linux_interface="eth1",
            ovs_port="vh12345",
            ovs_bridge="arch-ovs",
            vlan_tag=100,
        )
        test_db.add(mapping)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/interface-mappings", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["mappings"]) >= 1

    def test_node_specific_interfaces(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        sample_node_definitions: list[models.Node],
    ):
        """Returns interfaces for a specific node."""
        node = sample_node_definitions[0]
        mapping = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=node.id,
            linux_interface="eth1",
            ovs_port="vh12345",
            ovs_bridge="arch-ovs",
        )
        test_db.add(mapping)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/{node.gui_id}/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_empty_mappings(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns empty mappings when none exist."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/interface-mappings", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["mappings"] == []

    def test_node_not_found(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns 404 when node does not exist."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/nodes/nonexistent/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# TestInfraNotifications
# ===========================================================================


class TestInfraNotifications:
    """GET /labs/{id}/infra/notifications"""

    def test_clean_no_notifications(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Returns empty notifications when infra is healthy."""
        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notifications"] == []

    def test_tunnel_cleanup_notification(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        multiple_hosts: list[models.Host],
    ):
        """Returns cleanup notification for tunnel in cleanup state."""
        ls = _make_link_state(
            test_db,
            sample_lab.id,
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=ls.id,
            vni=10001,
            vlan_tag=200,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="10.0.0.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="10.0.0.2",
            status="cleanup",
            error_message="Cleanup deferred",
        )
        test_db.add(tunnel)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) >= 1
        categories = [n["category"] for n in data["notifications"]]
        assert "tunnel_cleanup" in categories

    def test_failed_tunnel_notification(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
        multiple_hosts: list[models.Host],
    ):
        """Returns error notification for failed tunnel."""
        ls = _make_link_state(
            test_db,
            sample_lab.id,
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=ls.id,
            vni=10002,
            vlan_tag=201,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="10.0.0.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="10.0.0.2",
            status="failed",
            error_message="VXLAN port creation failed",
        )
        test_db.add(tunnel)
        test_db.commit()

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["notifications"]) >= 1
        severities = [n["severity"] for n in data["notifications"]]
        assert "error" in severities

    def test_link_and_node_error_notifications(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns notifications for link and node errors."""
        _make_link_state(
            test_db,
            sample_lab.id,
            actual_state="error",
            error_message="VLAN tag mismatch",
        )
        _make_node_state(
            test_db,
            sample_lab.id,
            actual_state="error",
            error_message="Container crash",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/infra/notifications",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        categories = [n["category"] for n in data["notifications"]]
        assert "link_error" in categories
        assert "node_error" in categories


# ===========================================================================
# TestReconcileLinks
# ===========================================================================


class TestReconcileLinks:
    """POST /labs/{id}/links/reconcile"""

    def test_triggers_link_reconciliation(
        self,
        test_client: TestClient,
        auth_headers: dict,
        sample_lab: models.Lab,
    ):
        """Triggers link reconciliation and returns results."""
        with patch(
            "app.routers.labs.reconcile_lab_links",
            new_callable=AsyncMock,
            return_value={
                "checked": 5,
                "valid": 4,
                "repaired": 1,
                "errors": 0,
                "skipped": 0,
            },
        ):
            resp = test_client.post(
                f"/labs/{sample_lab.id}/links/reconcile",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["checked"] == 5
        assert data["valid"] == 4
        assert data["repaired"] == 1


# ===========================================================================
# TestExportInventory
# ===========================================================================


class TestExportInventory:
    """GET /labs/{id}/inventory"""

    def test_json_format(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns inventory in JSON format with node entries."""
        _make_node_state(
            test_db,
            sample_lab.id,
            node_name="R1",
            management_ip="172.20.0.2",
        )

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/inventory?format=json",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "json"
        assert len(data["nodes"]) >= 1
        assert data["nodes"][0]["node_name"] == "R1"

    def test_ansible_format(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns inventory in Ansible YAML format with content field."""
        _make_node_state(
            test_db,
            sample_lab.id,
            node_name="R1",
            management_ip="172.20.0.2",
        )

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/inventory?format=ansible",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "ansible"
        assert data["content"] is not None
        assert "all" in data["content"]

    def test_terraform_format(
        self,
        test_client: TestClient,
        auth_headers: dict,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        """Returns inventory in Terraform JSON format with content field."""
        _make_node_state(
            test_db,
            sample_lab.id,
            node_name="R1",
            management_ip="172.20.0.2",
        )

        with patch("app.routers.labs.TopologyService") as MockTS:
            mock_svc = MockTS.return_value
            mock_svc.has_nodes.return_value = False
            resp = test_client.get(
                f"/labs/{sample_lab.id}/inventory?format=terraform",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "terraform"
        assert data["content"] is not None
        parsed_content = json.loads(data["content"])
        assert "lab_nodes" in parsed_content
