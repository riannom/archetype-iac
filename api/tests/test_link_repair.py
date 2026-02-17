from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.tasks.link_repair as link_repair


@pytest.mark.asyncio
async def test_partial_recovery_requires_validation_when_both_sides_attached(
    test_db, sample_cross_host_link_state, multiple_hosts,
):
    link = sample_cross_host_link_state
    link.actual_state = "error"
    link.source_vxlan_attached = True
    link.target_vxlan_attached = True
    link.error_message = "previous failure"
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_repair,
        "verify_link_connected",
        new_callable=AsyncMock,
        return_value=(False, "TUNNEL_MISSING: vxlan port not found"),
    ) as mock_verify, patch.object(
        link_repair.agent_client,
        "attach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ) as mock_attach:
        recovered = await link_repair.attempt_partial_recovery(
            test_db, link, host_to_agent
        )

    assert recovered is False
    assert link.actual_state == "error"
    assert "validation failed" in (link.error_message or "").lower()
    mock_verify.assert_awaited_once()
    mock_attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_recovery_accepts_valid_dataplane_when_both_sides_attached(
    test_db, sample_cross_host_link_state, multiple_hosts,
):
    link = sample_cross_host_link_state
    link.actual_state = "error"
    link.source_vxlan_attached = True
    link.target_vxlan_attached = True
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_repair,
        "verify_link_connected",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as mock_verify, patch.object(
        link_repair.agent_client,
        "attach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ) as mock_attach:
        recovered = await link_repair.attempt_partial_recovery(
            test_db, link, host_to_agent
        )

    assert recovered is True
    assert link.actual_state == "up"
    assert link.source_carrier_state == "on"
    assert link.target_carrier_state == "on"
    assert link.error_message is None
    mock_verify.assert_awaited_once()
    mock_attach.assert_not_awaited()
