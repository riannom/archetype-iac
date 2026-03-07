"""Extended tests for app/tasks/link_orchestration.py — gaps not covered by test_link_orchestration.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    test_db: Session,
    lab: models.Lab,
    host: models.Host,
    *,
    gui_id: str,
    display_name: str,
    container_name: str,
    device: str = "linux",
    node_type: str = "device",
    managed_interface_id: str | None = None,
) -> models.Node:
    node = models.Node(
        id=str(uuid4()),
        lab_id=lab.id,
        gui_id=gui_id,
        display_name=display_name,
        container_name=container_name,
        device=device,
        node_type=node_type,
        managed_interface_id=managed_interface_id,
        host_id=host.id,
    )
    test_db.add(node)
    return node


def _make_link(
    test_db: Session,
    lab: models.Lab,
    source: models.Node,
    source_iface: str,
    target: models.Node,
    target_iface: str,
) -> models.Link:
    link = models.Link(
        id=str(uuid4()),
        lab_id=lab.id,
        link_name=f"{source.display_name}:{source_iface}-{target.display_name}:{target_iface}",
        source_node_id=source.id,
        source_interface=source_iface,
        target_node_id=target.id,
        target_interface=target_iface,
    )
    test_db.add(link)
    return link


def _make_link_state(
    test_db: Session,
    lab: models.Lab,
    *,
    link_name: str,
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
    source_host_id: str | None = None,
    target_host_id: str | None = None,
    actual_state: str = "pending",
    desired_state: str = "up",
    is_cross_host: bool = False,
    link_definition_id: str | None = None,
) -> models.LinkState:
    ls = models.LinkState(
        id=str(uuid4()),
        lab_id=lab.id,
        link_definition_id=link_definition_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        actual_state=actual_state,
        desired_state=desired_state,
        is_cross_host=is_cross_host,
    )
    test_db.add(ls)
    return ls


# ---------------------------------------------------------------------------
# 1. create_external_network_links
# ---------------------------------------------------------------------------

class TestCreateExternalNetworkLinksExtended:
    """Extended coverage for create_external_network_links."""

    @pytest.mark.asyncio
    async def test_agent_unavailable_marks_all_links_error(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """When the agent for the external interface is missing, all links fail."""
        from app.tasks.link_orchestration import create_external_network_links

        managed_iface = models.AgentManagedInterface(
            id=str(uuid4()),
            host_id=sample_host.id,
            name="eth99",
            interface_type="external",
            sync_status="synced",
            is_up=True,
        )
        device_node = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        ext_node = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="ext1", display_name="External", container_name="ext1",
            node_type="external", managed_interface_id=managed_iface.id,
        )
        link_def = _make_link(test_db, sample_lab, device_node, "eth1", ext_node, "_external")
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="r1:eth1-_ext:ext1:_external",
            source_node="r1", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
        )
        test_db.add(managed_iface)
        test_db.commit()

        # Pass an empty host_to_agent so the agent lookup fails
        ok, failed = await create_external_network_links(
            test_db,
            sample_lab.id,
            ext_node.id,
            [(link_def, link_state, device_node, ext_node, "eth1")],
            host_to_agent={},
            log_parts=[],
        )

        assert ok == 0
        assert failed == 1
        assert link_state.actual_state == "error"
        assert "agent" in (link_state.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_multiple_links_same_external_node_share_vlan(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Two devices on the same external node should share one VLAN (agent re-uses tag)."""
        from app.tasks.link_orchestration import create_external_network_links

        managed_iface = models.AgentManagedInterface(
            id=str(uuid4()),
            host_id=sample_host.id,
            name="eth99",
            interface_type="external",
            sync_status="synced",
            is_up=True,
        )
        device1 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        device2 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n2", display_name="R2", container_name="r2",
        )
        ext_node = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="ext1", display_name="External", container_name="ext1",
            node_type="external", managed_interface_id=managed_iface.id,
        )
        link_def1 = _make_link(test_db, sample_lab, device1, "eth1", ext_node, "_external")
        link_def2 = _make_link(test_db, sample_lab, device2, "eth1", ext_node, "_external")
        ls1 = _make_link_state(
            test_db, sample_lab,
            link_name="r1:eth1-_ext:eth99:_external",
            source_node="r1", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )
        ls2 = _make_link_state(
            test_db, sample_lab,
            link_name="r2:eth1-_ext:eth99:_external",
            source_node="r2", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )
        test_db.add_all([managed_iface])
        test_db.add_all([
            models.NodeState(
                lab_id=sample_lab.id, node_id="n1", node_name="r1",
                desired_state="running", actual_state="running",
            ),
            models.NodeState(
                lab_id=sample_lab.id, node_id="n2", node_name="r2",
                desired_state="running", actual_state="running",
            ),
        ])
        test_db.commit()

        call_count = 0

        async def connect_mock(*, agent, lab_id, node_name, interface_name, external_interface, vlan_tag):
            nonlocal call_count
            call_count += 1
            # First call: agent allocates VLAN 500
            # Second call: called with vlan_tag=500 (reuse)
            if call_count == 1:
                assert vlan_tag is None
                return {"success": True, "vlan_tag": 500}
            else:
                assert vlan_tag == 500
                return {"success": True, "vlan_tag": 500}

        with patch(
            "app.tasks.link_orchestration.agent_client.connect_external_on_agent",
            side_effect=connect_mock,
        ):
            ok, failed = await create_external_network_links(
                test_db,
                sample_lab.id,
                ext_node.id,
                [
                    (link_def1, ls1, device1, ext_node, "eth1"),
                    (link_def2, ls2, device2, ext_node, "eth1"),
                ],
                host_to_agent={sample_host.id: sample_host},
                log_parts=[],
            )

        assert ok == 2
        assert failed == 0
        assert ls1.actual_state == "up"
        assert ls2.actual_state == "up"
        assert ls1.vlan_tag == 500
        assert ls2.vlan_tag == 500

    @pytest.mark.asyncio
    async def test_connect_external_failure_marks_link_error(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """When the agent returns success=False, the link_state should be error."""
        from app.tasks.link_orchestration import create_external_network_links

        managed_iface = models.AgentManagedInterface(
            id=str(uuid4()),
            host_id=sample_host.id,
            name="eth99",
            interface_type="external",
            sync_status="synced",
            is_up=True,
        )
        device_node = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        ext_node = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="ext1", display_name="External", container_name="ext1",
            node_type="external", managed_interface_id=managed_iface.id,
        )
        link_def = _make_link(test_db, sample_lab, device_node, "eth1", ext_node, "_external")
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="r1:eth1-_ext:eth99:_external",
            source_node="r1", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )
        test_db.add(managed_iface)
        test_db.commit()

        with patch(
            "app.tasks.link_orchestration.agent_client.connect_external_on_agent",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "OVS port not found"},
        ):
            ok, failed = await create_external_network_links(
                test_db,
                sample_lab.id,
                ext_node.id,
                [(link_def, link_state, device_node, ext_node, "eth1")],
                host_to_agent={sample_host.id: sample_host},
                log_parts=[],
            )

        assert ok == 0
        assert failed == 1
        assert link_state.actual_state == "error"
        assert "OVS port not found" in (link_state.error_message or "")

    @pytest.mark.asyncio
    async def test_cross_host_external_link_creates_vxlan_tunnel(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """A remote device connected to an external NIC should persist VXLAN state."""
        from app.tasks.link_orchestration import create_external_network_links

        host_a, host_b = multiple_hosts[:2]
        managed_iface = models.AgentManagedInterface(
            id=str(uuid4()),
            host_id=host_a.id,
            name="eth99",
            interface_type="external",
            sync_status="synced",
            is_up=True,
        )
        ext_node = _make_node(
            test_db, sample_lab, host_a,
            gui_id="ext1", display_name="External", container_name="ext1",
            node_type="external", managed_interface_id=managed_iface.id,
        )
        device_node = _make_node(
            test_db, sample_lab, host_b,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        link_def = _make_link(test_db, sample_lab, device_node, "eth1", ext_node, "_external")
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="r1:eth1-_ext:eth99:_external",
            source_node="r1", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
            source_host_id=host_b.id, target_host_id=host_a.id,
            is_cross_host=True,
            link_definition_id=link_def.id,
        )
        test_db.add(managed_iface)
        test_db.commit()

        with patch("app.tasks.link_orchestration.agent_client.setup_cross_host_link_v2", new_callable=AsyncMock) as mock_setup, \
             patch("app.tasks.link_orchestration.agent_client.resolve_agent_ip", new_callable=AsyncMock) as mock_resolve:
            mock_setup.return_value = {"success": True, "vni": 9001}
            mock_resolve.side_effect = lambda addr: addr.split(":")[0]

            ok, failed = await create_external_network_links(
                test_db,
                sample_lab.id,
                ext_node.id,
                [(link_def, link_state, device_node, ext_node, "eth1")],
                host_to_agent={host_a.id: host_a, host_b.id: host_b},
                log_parts=[],
            )

        assert ok == 1
        assert failed == 0
        assert link_state.actual_state == "up"
        assert link_state.vni == 9001
        assert link_state.source_vxlan_attached is True
        assert link_state.target_vxlan_attached is True
        mock_setup.assert_awaited_once()
        _, kwargs = mock_setup.await_args
        assert kwargs["agent_a"].id == host_a.id
        assert kwargs["agent_b"].id == host_b.id
        assert kwargs["node_a"] == "_ext:eth99"
        assert kwargs["node_b"] == "r1"

    @pytest.mark.asyncio
    async def test_cross_host_external_link_remote_agent_missing_marks_error(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Cross-host external links should fail cleanly when the remote agent is unavailable."""
        from app.tasks.link_orchestration import create_external_network_links

        host_a, host_b = multiple_hosts[:2]
        managed_iface = models.AgentManagedInterface(
            id=str(uuid4()),
            host_id=host_a.id,
            name="eth99",
            interface_type="external",
            sync_status="synced",
            is_up=True,
        )
        ext_node = _make_node(
            test_db, sample_lab, host_a,
            gui_id="ext1", display_name="External", container_name="ext1",
            node_type="external", managed_interface_id=managed_iface.id,
        )
        device_node = _make_node(
            test_db, sample_lab, host_b,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        link_def = _make_link(test_db, sample_lab, device_node, "eth1", ext_node, "_external")
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="r1:eth1-_ext:eth99:_external",
            source_node="r1", source_interface="eth1",
            target_node="_ext:eth99", target_interface="_external",
            source_host_id=host_b.id, target_host_id=host_a.id,
            is_cross_host=True,
            link_definition_id=link_def.id,
        )
        test_db.add(managed_iface)
        test_db.commit()

        ok, failed = await create_external_network_links(
            test_db,
            sample_lab.id,
            ext_node.id,
            [(link_def, link_state, device_node, ext_node, "eth1")],
            host_to_agent={host_a.id: host_a},
            log_parts=[],
        )

        assert ok == 0
        assert failed == 1
        assert link_state.actual_state == "error"
        assert "remote agent not available" in (link_state.error_message or "").lower()


# ---------------------------------------------------------------------------
# 2. create_deployment_links — extended gaps
# ---------------------------------------------------------------------------

class TestCreateDeploymentLinksExtended:
    """Extended tests for create_deployment_links focusing on gap scenarios."""

    @pytest.mark.asyncio
    async def test_normalized_interface_names_stored_in_link_state(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Interface names should be normalised (Ethernet1 -> eth1) in the created LinkState."""
        from app.tasks.link_orchestration import create_deployment_links

        node1 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1", device="ceos",
        )
        node2 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n2", display_name="R2", container_name="r2", device="ceos",
        )
        _make_link(test_db, sample_lab, node1, "Ethernet1", node2, "Ethernet2")
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock), \
             patch("app.tasks.link_orchestration.normalize_interface", side_effect=lambda iface, dev: f"eth{iface[-1]}"):
            mock_client.create_link_on_agent = AsyncMock(return_value={"success": True, "vlan_tag": 10})

            success, fail = await create_deployment_links(
                test_db, sample_lab.id, host_to_agent
            )

        assert success == 1
        assert fail == 0

        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        # Normalisation should have converted Ethernet1/2 to eth1/eth2
        assert ls.source_interface == "eth1"
        assert ls.target_interface == "eth2"

    @pytest.mark.asyncio
    async def test_both_endpoints_not_running_deferred_to_pending(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """When both endpoints are stopped, the link is deferred (not a failure)."""
        from app.tasks.link_orchestration import create_deployment_links

        node1 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        node2 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n2", display_name="R2", container_name="r2",
        )
        _make_link(test_db, sample_lab, node1, "eth1", node2, "eth1")
        test_db.add_all([
            models.NodeState(
                lab_id=sample_lab.id, node_id="n1", node_name="r1",
                desired_state="running", actual_state="stopped",
            ),
            models.NodeState(
                lab_id=sample_lab.id, node_id="n2", node_name="r2",
                desired_state="running", actual_state="stopped",
            ),
        ])
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}
        log_parts: list[str] = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock()

            success, fail = await create_deployment_links(
                test_db, sample_lab.id, host_to_agent, log_parts=log_parts
            )

        mock_client.create_link_on_agent.assert_not_awaited()
        assert success == 0
        assert fail == 0

        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        assert ls.actual_state == "pending"
        assert any("PENDING" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_missing_node_reference_counted_as_failure(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """A link whose node IDs don't exist in the DB should count as one failure."""
        from app.tasks.link_orchestration import create_deployment_links

        # Link with non-existent node IDs
        orphan_link = models.Link(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="ghost:eth1-ghost2:eth1",
            source_node_id=str(uuid4()),
            source_interface="eth1",
            target_node_id=str(uuid4()),
            target_interface="eth1",
        )
        test_db.add(orphan_link)
        test_db.commit()

        success, fail = await create_deployment_links(
            test_db, sample_lab.id, {sample_host.id: sample_host}
        )

        assert success == 0
        assert fail == 1

    @pytest.mark.asyncio
    async def test_existing_link_state_reused_for_same_link_definition(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """An existing LinkState tied to a link_definition_id should be reused, not duplicated."""
        from app.tasks.link_orchestration import create_deployment_links

        node1 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="R1", container_name="r1",
        )
        node2 = _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n2", display_name="R2", container_name="r2",
        )
        link = _make_link(test_db, sample_lab, node1, "eth1", node2, "eth1")
        test_db.flush()

        # Pre-existing LinkState tied to this link_definition
        existing_ls = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_definition_id=link.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            actual_state="down",
            desired_state="up",
        )
        test_db.add(existing_ls)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.create_link_on_agent = AsyncMock(
                return_value={"success": True, "vlan_tag": 77}
            )

            success, fail = await create_deployment_links(
                test_db, sample_lab.id, host_to_agent
            )

        assert success == 1
        assert fail == 0

        all_states = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).all()
        # Only one LinkState should exist (the pre-existing one was reused)
        assert len(all_states) == 1
        assert all_states[0].id == existing_ls.id
        assert all_states[0].actual_state == "up"


# ---------------------------------------------------------------------------
# 3. create_same_host_link — extended gaps
# ---------------------------------------------------------------------------

class TestCreateSameHostLinkExtended:
    """Extended tests for create_same_host_link."""

    @pytest.mark.asyncio
    async def test_interface_normalization_passed_to_agent(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Vendor interface names (Ethernet3) stored in LinkState should be
        normalised to eth3 before being sent to create_link_on_agent."""
        from app.tasks.link_orchestration import create_same_host_link

        # Store vendor names in LinkState (as would happen for cEOS devices)
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:Ethernet3-R2:Ethernet3",
            source_node="r1", source_interface="Ethernet3",
            target_node="r2", target_interface="Ethernet3",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}
        called_kwargs: dict = {}

        async def capture_create_link(agent, **kwargs):
            called_kwargs.update(kwargs)
            return {"success": True, "vlan_tag": 42}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock), \
             patch("app.tasks.link_orchestration.normalize_interface", side_effect=lambda iface, dev: f"eth{iface[-1]}"):
            mock_client.create_link_on_agent = capture_create_link

            result = await create_same_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is True
        assert called_kwargs.get("source_interface") == "eth3"
        assert called_kwargs.get("target_interface") == "eth3"

    @pytest.mark.asyncio
    async def test_agent_exception_marks_link_error(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """An unexpected exception from the agent should mark the link as error."""
        from app.tasks.link_orchestration import create_same_host_link

        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R2:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r2", target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}
        log_parts: list[str] = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock(
                side_effect=ConnectionError("agent unreachable")
            )

            result = await create_same_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, log_parts
            )

        assert result is False
        assert link_state.actual_state == "error"
        assert "agent unreachable" in (link_state.error_message or "")
        assert any("FAILED" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_verify_failure_marks_link_error_and_returns_false(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """When verify_link_connected returns False, the link must be marked error."""
        from app.tasks.link_orchestration import create_same_host_link

        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R2:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r2", target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch(
                 "app.tasks.link_orchestration.verify_link_connected",
                 new_callable=AsyncMock,
                 return_value=(False, "VLAN tag mismatch"),
             ):
            mock_client.create_link_on_agent = AsyncMock(
                return_value={"success": True, "vlan_tag": 99}
            )

            result = await create_same_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is False
        assert link_state.actual_state == "error"
        assert "Verification failed" in (link_state.error_message or "")

    @pytest.mark.asyncio
    async def test_logs_warning_when_agent_lacks_ovs_port_hints(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Successful same-host create should warn when durable mappings remain missing."""
        from app.tasks.link_orchestration import create_same_host_link

        sample_host.version = "0.5.1-rc1"
        sample_host.git_sha = "2a2e4f7b18b512faf1802f60303a0e8a77a0e0d1"

        _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n1", display_name="CEOS-5", container_name="ceos_5", device="ceos",
        )
        _make_node(
            test_db, sample_lab, sample_host,
            gui_id="n2", display_name="CISCO_N9KV-4", container_name="cisco_n9kv_4", device="cisco_n9kv",
        )
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="ceos_5:eth4-cisco_n9kv_4:eth1",
            source_node="ceos_5", source_interface="eth4",
            target_node="cisco_n9kv_4", target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch(
                 "app.tasks.link_orchestration.verify_link_connected",
                 new_callable=AsyncMock,
                 return_value=(True, None),
             ), \
             patch(
                 "app.tasks.link_orchestration.update_interface_mappings",
                 new_callable=AsyncMock,
             ), \
             patch("app.tasks.link_orchestration.logger.warning") as warning_mock:
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "link": {
                    "vlan_tag": 2053,
                },
            })

            result = await create_same_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is True
        warning_mock.assert_called_once()
        warning_msg = warning_mock.call_args[0][0]
        assert "Agent upgrade may be required" in warning_msg
        assert "InterfaceMapping is still missing" in warning_msg


# ---------------------------------------------------------------------------
# 4. create_cross_host_link — extended gaps
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinkExtended:
    """Extended coverage for create_cross_host_link."""

    @pytest.mark.asyncio
    async def test_vxlan_tunnel_record_created_on_success(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """A VxlanTunnel row must be persisted when the tunnel is set up successfully."""
        from app.tasks.link_orchestration import create_cross_host_link

        host_a, host_b = multiple_hosts[:2]
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            is_cross_host=True,
        )
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts[:2]}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.setup_cross_host_link_v2 = AsyncMock(
                return_value={"success": True, "vni": 7777}
            )
            mock_client.resolve_agent_ip = AsyncMock(
                side_effect=lambda addr: addr.split(":")[0]
            )

            result = await create_cross_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is True
        assert link_state.actual_state == "up"
        assert link_state.vni == 7777

        test_db.flush()
        tunnel = (
            test_db.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == link_state.id)
            .first()
        )
        assert tunnel is not None
        assert tunnel.vni == 7777
        assert tunnel.status == "active"

    @pytest.mark.asyncio
    async def test_existing_vxlan_tunnel_updated_not_duplicated(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Calling create_cross_host_link twice should update the existing tunnel row,
        not create a second one."""
        from app.tasks.link_orchestration import create_cross_host_link

        host_a, host_b = multiple_hosts[:2]
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            is_cross_host=True,
        )
        test_db.flush()

        # Pre-existing tunnel for this link_state
        pre_tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link_state.id,
            vni=1111,
            vlan_tag=0,
            agent_a_id=host_a.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=host_b.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(pre_tunnel)
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts[:2]}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.setup_cross_host_link_v2 = AsyncMock(
                return_value={"success": True, "vni": 2222}
            )
            mock_client.resolve_agent_ip = AsyncMock(
                side_effect=lambda addr: addr.split(":")[0]
            )

            result = await create_cross_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is True
        test_db.flush()

        tunnels = (
            test_db.query(models.VxlanTunnel)
            .filter(models.VxlanTunnel.link_state_id == link_state.id)
            .all()
        )
        # Must be exactly one — the pre-existing row updated, not a duplicate
        assert len(tunnels) == 1
        test_db.refresh(pre_tunnel)
        assert pre_tunnel.vni == 2222

    @pytest.mark.asyncio
    async def test_both_agents_missing_marks_error(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """When neither source nor target agent is in host_to_agent, the link errors."""
        from app.tasks.link_orchestration import create_cross_host_link

        host_a, host_b = multiple_hosts[:2]
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            is_cross_host=True,
        )
        test_db.commit()

        result = await create_cross_host_link(
            test_db, sample_lab.id, link_state, {}, []
        )

        assert result is False
        assert link_state.actual_state == "error"
        assert link_state.error_message is not None

    @pytest.mark.asyncio
    async def test_partial_state_error_prefix_preserved(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """When agent returns partial_state=True the error message is prefixed PARTIAL_STATE."""
        from app.tasks.link_orchestration import create_cross_host_link

        host_a, host_b = multiple_hosts[:2]
        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            is_cross_host=True,
        )
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts[:2]}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": False,
                "error": "side B OVS down",
                "partial_state": True,
            })

            result = await create_cross_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, []
            )

        assert result is False
        assert link_state.actual_state == "error"
        assert "PARTIAL_STATE" in (link_state.error_message or "")


# ---------------------------------------------------------------------------
# 5. teardown_deployment_links — extended gaps
# ---------------------------------------------------------------------------

class TestTeardownDeploymentLinksExtended:
    """Extended coverage for teardown_deployment_links."""

    @pytest.mark.asyncio
    async def test_empty_tunnel_set_returns_zero_zero(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """When there are no VXLAN tunnels, teardown is a no-op and returns (0, 0)."""
        from app.tasks.link_orchestration import teardown_deployment_links

        success, fail = await teardown_deployment_links(
            test_db, sample_lab.id, {}
        )
        assert success == 0
        assert fail == 0

    @pytest.mark.asyncio
    async def test_unreachable_agent_counted_as_failure(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """An agent not in host_to_agent should count as a failure, not a success."""
        from app.tasks.link_orchestration import teardown_deployment_links

        host_a, host_b = multiple_hosts[:2]
        ls = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            actual_state="up",
            is_cross_host=True,
        )
        test_db.flush()
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=ls.id,
            vni=9999,
            vlan_tag=0,
            agent_a_id=host_a.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=host_b.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        # Only provide host_a — host_b is unreachable
        host_to_agent = {host_a.id: host_a}
        log_parts: list[str] = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.cleanup_overlay_on_agent = AsyncMock(
                return_value={"tunnels_deleted": 1, "bridges_deleted": 0, "errors": []}
            )

            success, fail = await teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent, log_parts
            )

        # host_a cleaned up (success=1), host_b missing (fail=1)
        assert success == 1
        assert fail == 1
        assert any("not available" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_mixed_success_failure_preserves_failed_link_state(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """When one agent succeeds and one fails, the cross-host LinkState should be
        kept and marked error (not deleted)."""
        from app.tasks.link_orchestration import teardown_deployment_links

        host_a, host_b = multiple_hosts[:2]
        ls = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            actual_state="up",
            is_cross_host=True,
        )
        test_db.flush()
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=ls.id,
            vni=8888,
            vlan_tag=0,
            agent_a_id=host_a.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=host_b.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        host_to_agent = {host_a.id: host_a, host_b.id: host_b}
        call_number = 0

        async def patchy_cleanup(agent, lab_id):
            nonlocal call_number
            call_number += 1
            if call_number == 1:
                return {"tunnels_deleted": 1, "errors": []}
            raise Exception("network timeout")

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.cleanup_overlay_on_agent = patchy_cleanup

            success, fail = await teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent
            )

        assert success == 1
        assert fail == 1

        # The cross-host LinkState whose target agent failed should remain (error state)
        surviving_ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert surviving_ls is not None
        assert surviving_ls.actual_state == "error"

    @pytest.mark.asyncio
    async def test_successful_teardown_deletes_all_link_states(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """When all agents clean up successfully, every LinkState must be deleted."""
        from app.tasks.link_orchestration import teardown_deployment_links

        host_a, host_b = multiple_hosts[:2]
        # Create two link states: one cross-host, one same-host
        cross_ls = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R3:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r3", target_interface="eth1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            actual_state="up",
            is_cross_host=True,
        )
        _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth2-R1:eth3",
            source_node="r1", source_interface="eth2",
            target_node="r1", target_interface="eth3",
            source_host_id=host_a.id,
            target_host_id=host_a.id,
            actual_state="up",
        )
        test_db.flush()
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=cross_ls.id,
            vni=5555,
            vlan_tag=0,
            agent_a_id=host_a.id,
            agent_a_ip="10.0.0.1",
            agent_b_id=host_b.id,
            agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.cleanup_overlay_on_agent = AsyncMock(
                return_value={"tunnels_deleted": 1, "bridges_deleted": 0, "errors": []}
            )

            success, fail = await teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent
            )

        assert fail == 0
        remaining = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).all()
        assert remaining == []


# ---------------------------------------------------------------------------
# 6. _sync_oper_state
# ---------------------------------------------------------------------------

class TestSyncOperState:
    """Tests for the _sync_oper_state helper."""

    def test_delegates_to_recompute_link_oper_state(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """_sync_oper_state should call recompute_link_oper_state with the same args."""
        from app.tasks.link_orchestration import _sync_oper_state

        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R2:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r2", target_interface="eth1",
            actual_state="up",
        )
        test_db.commit()

        with patch(
            "app.tasks.link_orchestration.recompute_link_oper_state"
        ) as mock_recompute:
            _sync_oper_state(test_db, link_state)
            mock_recompute.assert_called_once_with(test_db, link_state)

    def test_does_not_raise_on_valid_inputs(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Calling _sync_oper_state with a valid session and link_state must not raise."""
        from app.tasks.link_orchestration import _sync_oper_state

        link_state = _make_link_state(
            test_db, sample_lab,
            link_name="R1:eth1-R2:eth1",
            source_node="r1", source_interface="eth1",
            target_node="r2", target_interface="eth1",
            actual_state="up",
        )
        test_db.commit()

        # Should execute without error even if recompute_link_oper_state is a no-op
        with patch("app.tasks.link_orchestration.recompute_link_oper_state"):
            _sync_oper_state(test_db, link_state)
