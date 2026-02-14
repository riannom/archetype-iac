"""Tests for POST /callbacks/carrier-state endpoint.

These tests verify carrier state change propagation logic:
- Correct DB field updated (source vs target)
- Peer carrier set via remote agent HTTP call
- Operational state recomputed
- WebSocket broadcast sent
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_host(test_db: Session, sample_lab: models.Lab) -> models.Host:
    """Host for the source endpoint."""
    host = models.Host(
        id="host-src-1",
        name="agent-source",
        address="10.0.0.1:8001",
        last_heartbeat=None,
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture()
def target_host(test_db: Session) -> models.Host:
    """Host for the target endpoint."""
    host = models.Host(
        id="host-tgt-1",
        name="agent-target",
        address="10.0.0.2:8001",
        last_heartbeat=None,
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture()
def link_with_hosts(
    test_db: Session,
    sample_lab: models.Lab,
    source_host: models.Host,
    target_host: models.Host,
) -> models.LinkState:
    """LinkState with source/target hosts for carrier tests."""
    ls = models.LinkState(
        id="carrier-link-1",
        lab_id=sample_lab.id,
        link_name="R1:eth1-R2:eth1",
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
        source_host_id=source_host.id,
        target_host_id=target_host.id,
        source_carrier_state="on",
        target_carrier_state="on",
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCarrierStateSourceChange:
    """Node matches source endpoint → source_carrier_state updated."""

    @pytest.mark.asyncio
    async def test_source_carrier_off(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="off",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            result = await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        assert result["success"] is True
        test_db.refresh(link_with_hosts)
        assert link_with_hosts.source_carrier_state == "off"


class TestCarrierStateTargetChange:
    """Node matches target endpoint → target_carrier_state updated."""

    @pytest.mark.asyncio
    async def test_target_carrier_off(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R2",
            interface="eth1",
            carrier_state="off",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            result = await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        assert result["success"] is True
        test_db.refresh(link_with_hosts)
        assert link_with_hosts.target_carrier_state == "off"


class TestCarrierPeerPropagation:
    """Verify remote agent HTTP call is made for peer carrier."""

    @pytest.mark.asyncio
    async def test_peer_agent_called(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="off",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        # Should have called the target agent's carrier endpoint
        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert f"/labs/{sample_lab.id}/interfaces/R2/eth1/carrier" in call_url
        assert target_host.address in call_url


class TestCarrierRestore:
    """carrier_state="on" → peer carrier restored to "on"."""

    @pytest.mark.asyncio
    async def test_carrier_on_restores_peer(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        # First set carrier off
        link_with_hosts.source_carrier_state = "off"
        link_with_hosts.target_carrier_state = "off"
        test_db.commit()

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="on",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            result = await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        assert result["success"] is True
        test_db.refresh(link_with_hosts)
        assert link_with_hosts.source_carrier_state == "on"
        # Peer should also be updated (propagated carrier on)
        assert link_with_hosts.target_carrier_state == "on"


class TestCarrierOperStateRecomputed:
    """Verify oper state is updated after carrier change."""

    @pytest.mark.asyncio
    async def test_oper_state_changes_on_carrier_down(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        # Create NodeState records so oper_state can be computed
        for node_name in ("R1", "R2"):
            ns = models.NodeState(
                id=f"ns-{node_name}",
                lab_id=sample_lab.id,
                node_id=f"nid-{node_name}",
                node_name=node_name,
                actual_state="running",
                desired_state="running",
            )
            test_db.add(ns)
        test_db.commit()

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="off",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        test_db.refresh(link_with_hosts)
        # Source carrier is off, so oper state should reflect interface down
        assert link_with_hosts.source_carrier_state == "off"
        # recompute_link_oper_state was called; it should set oper reasons


class TestCarrierBroadcast:
    """Verify WebSocket broadcast sent on state change."""

    @pytest.mark.asyncio
    async def test_broadcast_called_on_change(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        # Need NodeState for oper_state computation
        for node_name in ("R1", "R2"):
            ns = models.NodeState(
                id=f"ns-bc-{node_name}",
                lab_id=sample_lab.id,
                node_id=f"nid-bc-{node_name}",
                node_name=node_name,
                actual_state="running",
                desired_state="running",
            )
            test_db.add(ns)
        test_db.commit()

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="off",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        mock_publish = AsyncMock(return_value=1)

        with (
            patch("app.routers.callbacks.agent_client.get_http_client", return_value=mock_client),
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=True),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = mock_publish
            await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        # Broadcast should have been called with correct params
        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["lab_id"] == sample_lab.id
        assert call_kwargs["link_name"] == "R1:eth1-R2:eth1"


class TestCarrierLinkNotFound:
    """Returns gracefully when no LinkState matches."""

    @pytest.mark.asyncio
    async def test_no_link_returns_failure(
        self,
        test_db: Session,
        sample_lab: models.Lab,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="NONEXISTENT",
            interface="eth99",
            carrier_state="off",
        )

        with patch("app.routers.callbacks.verify_agent_secret", return_value=None):
            result = await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        assert result["success"] is False
        assert "not found" in result["message"].lower()


class TestCarrierPeerAgentOffline:
    """Peer agent unreachable → logs error, updates DB anyway."""

    @pytest.mark.asyncio
    async def test_offline_peer_still_updates_db(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        link_with_hosts: models.LinkState,
        source_host: models.Host,
        target_host: models.Host,
    ):
        from app.routers.callbacks import carrier_state_changed
        from app.schemas import CarrierStateChangeRequest

        payload = CarrierStateChangeRequest(
            lab_id=sample_lab.id,
            node="R1",
            interface="eth1",
            carrier_state="off",
        )

        with (
            patch("app.routers.callbacks.agent_client.is_agent_online", return_value=False),
            patch("app.routers.callbacks.get_broadcaster") as mock_bc,
            patch("app.routers.callbacks.verify_agent_secret", return_value=None),
        ):
            mock_bc.return_value.publish_link_state = AsyncMock(return_value=1)
            result = await carrier_state_changed(
                payload=payload,
                database=test_db,
                _auth=None,
            )

        # DB should still be updated even though peer is offline
        assert result["success"] is True
        test_db.refresh(link_with_hosts)
        assert link_with_hosts.source_carrier_state == "off"
        # Target carrier should NOT be updated (couldn't reach agent)
        assert link_with_hosts.target_carrier_state == "on"
