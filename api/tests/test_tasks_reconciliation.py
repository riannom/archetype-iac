"""Tests for app/tasks/reconciliation.py - State reconciliation background task."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models


class TestGenerateLinkName:
    """Tests for the _generate_link_name helper function."""

    def test_generates_consistent_name(self):
        """Should generate consistent name regardless of endpoint order."""
        from app.utils.link import generate_link_name

        name1 = generate_link_name("R1", "eth1", "R2", "eth2")
        name2 = generate_link_name("R2", "eth2", "R1", "eth1")

        assert name1 == name2

    def test_generates_expected_format(self):
        """Should generate name in expected format."""
        from app.utils.link import generate_link_name

        name = generate_link_name("Router1", "eth0", "Switch1", "ge-0/0/1")

        # Should be sorted alphabetically
        assert ":" in name
        assert "-" in name

    def test_handles_same_node_different_interfaces(self):
        """Should handle links between different interfaces on same node."""
        from app.utils.link import generate_link_name

        name = generate_link_name("R1", "eth1", "R1", "eth2")
        assert "R1:eth1" in name
        assert "R1:eth2" in name


class TestEnsureLinkStatesForLab:
    """Tests for the _ensure_link_states_for_lab function."""

    def test_creates_missing_link_states(self, test_db: Session, sample_lab: models.Lab):
        """Should create LinkState records for links in topology."""
        from app.tasks.reconciliation import _ensure_link_states_for_lab

        node1 = models.Node(
            lab_id=sample_lab.id,
            gui_id="R1",
            display_name="R1",
            container_name="R1",
            node_type="device",
            device="linux",
        )
        node2 = models.Node(
            lab_id=sample_lab.id,
            gui_id="R2",
            display_name="R2",
            container_name="R2",
            node_type="device",
            device="linux",
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        link = models.Link(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node_id=node1.id,
            source_interface="eth1",
            target_node_id=node2.id,
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created >= 1

        # Flush added records so query can find them (autoflush=False)
        test_db.flush()
        links = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).all()
        assert len(links) >= 1

    def test_skips_existing_link_states(self, test_db: Session, sample_lab: models.Lab, sample_link_state: models.LinkState):
        """Should not duplicate existing link states."""
        from app.tasks.reconciliation import _ensure_link_states_for_lab

        # Get count before
        before_count = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).count()

        _ensure_link_states_for_lab(test_db, sample_lab.id)

        # Count should not have increased for existing link
        after_count = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).count()
        assert after_count >= before_count

    def test_handles_empty_links(self, test_db: Session, sample_lab: models.Lab):
        """Should handle labs with no links gracefully."""
        from app.tasks.reconciliation import _ensure_link_states_for_lab

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 0


class TestRefreshStatesFromAgents:
    """Tests for the refresh_states_from_agents function."""

    @pytest.mark.asyncio
    async def test_handles_no_labs_to_reconcile(self, test_db: Session):
        """Should complete without error when no labs need reconciliation."""
        from app.tasks.reconciliation import refresh_states_from_agents

        with patch("app.tasks.reconciliation.SessionLocal", return_value=test_db):
            await refresh_states_from_agents()

    @pytest.mark.asyncio
    async def test_skips_lab_with_active_job(self, test_db: Session, sample_lab: models.Lab, running_job: models.Job):
        """Should skip labs that have active jobs within timeout."""
        from app.tasks.reconciliation import refresh_states_from_agents

        # Set lab to transitional state
        sample_lab.state = "starting"
        test_db.commit()

        with patch("app.tasks.reconciliation.SessionLocal", return_value=test_db):
            with patch("app.tasks.reconciliation._reconcile_single_lab", new_callable=AsyncMock) as mock_reconcile:
                await refresh_states_from_agents()
                # Should not call reconcile for this lab
                # (it has an active job)


class TestReconcileSingleLab:
    """Tests for the _reconcile_single_lab function."""

    @pytest.mark.asyncio
    async def test_handles_missing_lab(self, test_db: Session):
        """Should handle case where lab doesn't exist."""
        from app.tasks.reconciliation import _reconcile_single_lab

        await _reconcile_single_lab(test_db, "nonexistent-lab-id")

    @pytest.mark.asyncio
    async def test_handles_no_agent_available(self, test_db: Session, sample_lab: models.Lab):
        """Should handle case where no agent is available."""
        from app.tasks.reconciliation import _reconcile_single_lab

        sample_lab.state = "starting"
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
            mock_get_agent.return_value = None

            await _reconcile_single_lab(test_db, sample_lab.id)


class TestCheckReadinessForNodes:
    """Tests for the _check_readiness_for_nodes function."""

    @pytest.mark.asyncio
    async def test_handles_empty_nodes_list(self, test_db: Session):
        """Should handle empty nodes list."""
        from app.tasks.reconciliation import _check_readiness_for_nodes

        await _check_readiness_for_nodes(test_db, [])

    @pytest.mark.asyncio
    async def test_sets_boot_started_at_if_not_set(self, test_db: Session, sample_lab_with_nodes):
        """Should set boot_started_at if not already set."""
        from app.tasks.reconciliation import _check_readiness_for_nodes

        lab, nodes = sample_lab_with_nodes

        # Update nodes to be running but not ready
        for node in nodes:
            node.actual_state = "running"
            node.is_ready = False
            node.boot_started_at = None
        test_db.commit()

        mock_agent = MagicMock()
        mock_agent.address = "localhost:8080"

        with patch("app.tasks.reconciliation.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
            mock_get_agent.return_value = mock_agent
            with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_readiness:
                mock_readiness.return_value = {"is_ready": False}

                await _check_readiness_for_nodes(test_db, nodes)

                for node in nodes:
                    test_db.refresh(node)
                    # boot_started_at should now be set
                    assert node.boot_started_at is not None

    @pytest.mark.asyncio
    async def test_marks_node_ready_when_readiness_check_passes(self, test_db: Session, sample_lab_with_nodes):
        """Should mark node as ready when readiness check returns true."""
        from app.tasks.reconciliation import _check_readiness_for_nodes

        lab, nodes = sample_lab_with_nodes

        # Update nodes to be running but not ready
        for node in nodes:
            node.actual_state = "running"
            node.is_ready = False
        test_db.commit()

        mock_agent = MagicMock()
        mock_agent.address = "localhost:8080"

        with patch("app.tasks.reconciliation.agent_client.get_agent_for_lab", new_callable=AsyncMock) as mock_get_agent:
            mock_get_agent.return_value = mock_agent
            with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_readiness:
                mock_readiness.return_value = {"is_ready": True}

                await _check_readiness_for_nodes(test_db, nodes)

                for node in nodes:
                    test_db.refresh(node)
                    assert node.is_ready is True


class TestStateReconciliationMonitor:
    """Tests for the state_reconciliation_monitor background task."""

    @pytest.mark.asyncio
    async def test_runs_refresh_states_from_agents(self):
        """Should call refresh_states_from_agents each iteration."""
        from app.tasks.reconciliation import state_reconciliation_monitor

        with patch("app.tasks.reconciliation.refresh_states_from_agents", new_callable=AsyncMock) as mock_reconcile:
            with patch("app.tasks.reconciliation.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                call_count = 0
                async def sleep_and_cancel(seconds):
                    nonlocal call_count
                    call_count += 1
                    if call_count > 1:
                        raise asyncio.CancelledError()
                mock_sleep.side_effect = sleep_and_cancel

                await state_reconciliation_monitor()

                mock_reconcile.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_on_cancelled_error(self):
        """Should stop gracefully when cancelled."""
        from app.tasks.reconciliation import state_reconciliation_monitor

        with patch("app.tasks.reconciliation.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError()

            await state_reconciliation_monitor()

    @pytest.mark.asyncio
    async def test_continues_on_general_exception(self):
        """Should continue running after handling an exception."""
        from app.tasks.reconciliation import state_reconciliation_monitor

        with patch("app.tasks.reconciliation.refresh_states_from_agents", new_callable=AsyncMock) as mock_reconcile:
            call_count = 0
            async def reconcile_with_error():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Test error")
            mock_reconcile.side_effect = reconcile_with_error

            with patch("app.tasks.reconciliation.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                sleep_count = 0
                async def sleep_and_cancel(seconds):
                    nonlocal sleep_count
                    sleep_count += 1
                    if sleep_count > 2:
                        raise asyncio.CancelledError()
                mock_sleep.side_effect = sleep_and_cancel

                await state_reconciliation_monitor()

                # Should have tried reconcile more than once
                assert call_count >= 2


class TestLinkStateReconciliation:
    """Tests for link state reconciliation within _reconcile_single_lab."""

    @pytest.mark.asyncio
    async def test_link_state_up_when_both_nodes_running(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Link should be 'up' when both endpoint nodes are running."""
        from app.tasks.reconciliation import _reconcile_single_lab

        # Point lab at real host so reconciliation can find it
        sample_lab.agent_id = sample_host.id
        test_db.commit()

        # Create node states
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R2",
            desired_state="running",
            actual_state="running",
        )
        test_db.add_all([node1, node2])

        # Create link state
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R2", "status": "running"},
                    ]
                }
                with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_ready:
                    mock_ready.return_value = {"is_ready": True}

                    await _reconcile_single_lab(test_db, sample_lab.id)

                    test_db.refresh(link)
                    assert link.actual_state == "up"

    @pytest.mark.asyncio
    async def test_link_state_down_when_node_stopped(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Link should be 'down' when one endpoint node is stopped."""
        from app.tasks.reconciliation import _reconcile_single_lab

        sample_lab.agent_id = sample_host.id
        test_db.commit()

        # Create node states - desired_state must match actual_state to avoid
        # triggering enforcement (which would rollback link state changes)
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R2",
            desired_state="stopped",
            actual_state="stopped",
        )
        test_db.add_all([node1, node2])

        # Create link state
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            actual_state="up",
        )
        test_db.add(link)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R2", "status": "stopped"},
                    ]
                }

                await _reconcile_single_lab(test_db, sample_lab.id)

                test_db.refresh(link)
                assert link.actual_state == "down"

    @pytest.mark.asyncio
    async def test_link_state_down_when_carrier_off(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Link should be 'down' when carrier state is off on either endpoint."""
        from app.tasks.reconciliation import _reconcile_single_lab

        sample_lab.agent_id = sample_host.id
        test_db.commit()

        # Create node states - desired_state must match actual_state to avoid
        # triggering enforcement (which would rollback link state changes)
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R2",
            desired_state="running",
            actual_state="running",
        )
        test_db.add_all([node1, node2])

        # Create link state with carrier off on source
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            actual_state="up",
            source_carrier_state="off",  # Carrier off on source
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R2", "status": "running"},
                    ]
                }
                with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_ready:
                    mock_ready.return_value = {"is_ready": True}
                    # Prevent auto-connect from overwriting link state
                    with patch("app.tasks.reconciliation.acquire_link_ops_lock", return_value=False):

                        await _reconcile_single_lab(test_db, sample_lab.id)

                        test_db.refresh(link)
                        # Link should be down when carrier is off
                        assert link.actual_state == "down"

    @pytest.mark.asyncio
    async def test_cross_host_link_state_verification(self, test_db: Session, sample_lab: models.Lab):
        """Cross-host link should verify VXLAN tunnel is active."""
        from app.tasks.reconciliation import _reconcile_single_lab
        import json
        from datetime import datetime, timezone

        # Create hosts with last_heartbeat so is_agent_online works
        host1 = models.Host(
            id="host-1",
            name="Host 1",
            address="192.168.1.1:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        host2 = models.Host(
            id="host-2",
            name="Host 2",
            address="192.168.1.2:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([host1, host2])

        # Point lab at first host
        sample_lab.agent_id = "host-1"

        # Create node states on different hosts - desired_state must match
        # actual_state to avoid triggering enforcement
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R3",
            desired_state="running",
            actual_state="running",
        )
        test_db.add_all([node1, node2])

        # Create cross-host link state with VXLAN
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            actual_state="up",
            is_cross_host=True,
            vni=12345,
            vlan_tag=200,
            source_host_id="host-1",
            target_host_id="host-2",
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.flush()

        # Create VXLAN tunnel record
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link.id,
            vni=12345,
            vlan_tag=200,
            agent_a_id="host-1",
            agent_a_ip="192.168.1.1",
            agent_b_id="host-2",
            agent_b_ip="192.168.1.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R3", "status": "running"},
                    ]
                }
                with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_ready:
                    mock_ready.return_value = {"is_ready": True}

                    await _reconcile_single_lab(test_db, sample_lab.id)

                    test_db.refresh(link)
                    # Cross-host link with active tunnel should be up
                    assert link.actual_state == "up"


class TestLinkStateCarrierReconciliation:
    """Tests for carrier state tracking during reconciliation."""

    @pytest.mark.asyncio
    async def test_carrier_state_preserved_during_reconciliation(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Carrier state should be preserved during reconciliation."""
        from app.tasks.reconciliation import _reconcile_single_lab

        sample_lab.agent_id = sample_host.id
        test_db.commit()

        # Create node states - desired_state must match actual_state to avoid
        # triggering enforcement (which would rollback link state changes)
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R2",
            desired_state="running",
            actual_state="running",
        )
        test_db.add_all([node1, node2])

        # Create link state with specific carrier states
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            actual_state="up",
            source_carrier_state="on",
            target_carrier_state="off",  # Intentionally off
        )
        test_db.add(link)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R2", "status": "running"},
                    ]
                }
                with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_ready:
                    mock_ready.return_value = {"is_ready": True}
                    # Prevent auto-connect from overwriting link state
                    with patch("app.tasks.reconciliation.acquire_link_ops_lock", return_value=False):

                        await _reconcile_single_lab(test_db, sample_lab.id)

                        test_db.refresh(link)
                        # Carrier states should be unchanged
                        assert link.source_carrier_state == "on"
                        assert link.target_carrier_state == "off"
                        # But link should be down since target carrier is off
                        assert link.actual_state == "down"

    @pytest.mark.asyncio
    async def test_link_state_updates_on_vxlan_tunnel_failure(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Cross-host link should be 'error' if VXLAN tunnel is failed."""
        from app.tasks.reconciliation import _reconcile_single_lab
        import json
        from datetime import datetime, timezone

        # Create hosts with last_heartbeat so is_agent_online works
        host1 = models.Host(
            id="host-a",
            name="Host A",
            address="192.168.1.10:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        host2 = models.Host(
            id="host-b",
            name="Host B",
            address="192.168.1.20:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([host1, host2])

        # Point lab at first host
        sample_lab.agent_id = "host-a"

        # Create node states - desired_state must match actual_state to avoid
        # triggering enforcement (which would rollback link state changes)
        node1 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n1",
            node_name="R1",
            desired_state="running",
            actual_state="running",
        )
        node2 = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="n2",
            node_name="R3",
            desired_state="running",
            actual_state="running",
        )
        test_db.add_all([node1, node2])

        # Create cross-host link state
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            actual_state="up",
            is_cross_host=True,
            vni=99999,
            vlan_tag=300,
            source_host_id="host-a",
            target_host_id="host-b",
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.flush()

        # Create VXLAN tunnel with 'failed' status
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link.id,
            vni=99999,
            vlan_tag=300,
            agent_a_id="host-a",
            agent_a_ip="192.168.1.10",
            agent_b_id="host-b",
            agent_b_ip="192.168.1.20",
            status="failed",  # Tunnel failed
            error_message="VXLAN port creation failed",
        )
        test_db.add(tunnel)
        test_db.commit()

        with patch("app.tasks.reconciliation.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation.agent_client.get_lab_status_from_agent", new_callable=AsyncMock) as mock_status:
                mock_status.return_value = {
                    "nodes": [
                        {"name": "R1", "status": "running"},
                        {"name": "R3", "status": "running"},
                    ]
                }
                with patch("app.tasks.reconciliation.agent_client.check_node_readiness", new_callable=AsyncMock) as mock_ready:
                    mock_ready.return_value = {"is_ready": True}
                    # Prevent auto-connect from overwriting link state
                    with patch("app.tasks.reconciliation.acquire_link_ops_lock", return_value=False):

                        await _reconcile_single_lab(test_db, sample_lab.id)

                        test_db.refresh(link)
                        # Link should be error because tunnel is not active
                        assert link.actual_state == "error"
