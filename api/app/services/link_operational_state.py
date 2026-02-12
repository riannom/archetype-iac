"""Deterministic link operational state calculator.

This module computes derived per-endpoint operational link state based on:
- user/admin intent
- local endpoint health
- remote endpoint health
- transport health (same-host or cross-host)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from sqlalchemy.orm import Session

from app import agent_client, models
from app.metrics import record_link_oper_transition

logger = logging.getLogger(__name__)


OPER_UP = "up"
OPER_DOWN = "down"

TRANSPORT_UP = "up"
TRANSPORT_DOWN = "down"
TRANSPORT_DEGRADED = "degraded"

REASON_ADMIN_DOWN = "admin_down"
REASON_LOCAL_NODE_DOWN = "local_node_down"
REASON_LOCAL_INTERFACE_DOWN = "local_interface_down"
REASON_PEER_HOST_OFFLINE = "peer_host_offline"
REASON_PEER_NODE_DOWN = "peer_node_down"
REASON_PEER_INTERFACE_DOWN = "peer_interface_down"
REASON_TRANSPORT_DOWN = "transport_down"
REASON_TRANSPORT_DEGRADED = "transport_degraded"
REASON_UNKNOWN = "unknown"


@dataclass(frozen=True)
class EndpointOperationalInput:
    """Input signals for one link endpoint."""

    admin_state: str
    local_node_running: bool
    local_interface_up: bool
    peer_host_online: bool
    peer_node_running: bool
    peer_interface_up: bool


@dataclass(frozen=True)
class EndpointOperationalState:
    """Computed operational state for one endpoint."""

    oper_state: str
    reason: str | None


def compute_endpoint_oper_state(
    endpoint: EndpointOperationalInput,
    transport_state: str,
) -> EndpointOperationalState:
    """Compute effective operational state for a single link endpoint.

    The result is intentionally strict: any failed prerequisite drives state down.
    """
    if endpoint.admin_state != "up":
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_ADMIN_DOWN)
    if not endpoint.local_node_running:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_LOCAL_NODE_DOWN)
    if not endpoint.local_interface_up:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_LOCAL_INTERFACE_DOWN)
    if not endpoint.peer_host_online:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_PEER_HOST_OFFLINE)
    if not endpoint.peer_node_running:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_PEER_NODE_DOWN)
    if not endpoint.peer_interface_up:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_PEER_INTERFACE_DOWN)
    if transport_state == TRANSPORT_DOWN:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_TRANSPORT_DOWN)
    if transport_state == TRANSPORT_DEGRADED:
        return EndpointOperationalState(
            oper_state=OPER_DOWN,
            reason=REASON_TRANSPORT_DEGRADED,
        )
    if transport_state != TRANSPORT_UP:
        return EndpointOperationalState(oper_state=OPER_DOWN, reason=REASON_UNKNOWN)
    return EndpointOperationalState(oper_state=OPER_UP, reason=None)


def compute_link_oper_states(
    source: EndpointOperationalInput,
    target: EndpointOperationalInput,
    transport_state: str,
) -> tuple[EndpointOperationalState, EndpointOperationalState]:
    """Compute operational state for both link endpoints."""
    return (
        compute_endpoint_oper_state(source, transport_state),
        compute_endpoint_oper_state(target, transport_state),
    )


def _is_external_endpoint(node_name: str | None) -> bool:
    return bool(node_name and node_name.startswith("_ext:"))


def _is_node_running(
    session: Session,
    lab_id: str,
    node_name: str | None,
) -> bool:
    # External endpoints do not have NodeState rows; treat as available.
    if _is_external_endpoint(node_name):
        return True
    if not node_name:
        return False
    state = (
        session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_name == node_name,
        )
        .first()
    )
    return bool(state and state.actual_state == "running")


def _is_host_online(session: Session, host_id: str | None) -> bool:
    if not host_id:
        return False
    host = session.get(models.Host, host_id)
    return bool(host and agent_client.is_agent_online(host))


def _carrier_up(value: str | None) -> bool:
    return (value or "").lower() == "on"


def _transport_state(link_state: models.LinkState) -> str:
    if link_state.is_cross_host:
        source_attached = bool(link_state.source_vxlan_attached)
        target_attached = bool(link_state.target_vxlan_attached)
        if source_attached and target_attached and link_state.actual_state == "up":
            return TRANSPORT_UP
        if source_attached or target_attached:
            return TRANSPORT_DEGRADED
        if link_state.actual_state == "error":
            return TRANSPORT_DEGRADED
        return TRANSPORT_DOWN
    if link_state.actual_state == "up":
        return TRANSPORT_UP
    if link_state.actual_state == "error":
        return TRANSPORT_DEGRADED
    return TRANSPORT_DOWN


def recompute_link_oper_state(
    session: Session,
    link_state: models.LinkState,
) -> bool:
    """Recompute and persist derived operational state for both endpoints.

    Returns True when any endpoint operational field changed.
    """
    transport = _transport_state(link_state)

    source_node_running = _is_node_running(session, link_state.lab_id, link_state.source_node)
    target_node_running = _is_node_running(session, link_state.lab_id, link_state.target_node)
    source_host_online = _is_host_online(session, link_state.source_host_id)
    target_host_online = _is_host_online(session, link_state.target_host_id)

    source_input = EndpointOperationalInput(
        admin_state=link_state.desired_state,
        local_node_running=source_node_running,
        local_interface_up=_carrier_up(link_state.source_carrier_state),
        peer_host_online=target_host_online,
        peer_node_running=target_node_running,
        peer_interface_up=_carrier_up(link_state.target_carrier_state),
    )
    target_input = EndpointOperationalInput(
        admin_state=link_state.desired_state,
        local_node_running=target_node_running,
        local_interface_up=_carrier_up(link_state.target_carrier_state),
        peer_host_online=source_host_online,
        peer_node_running=source_node_running,
        peer_interface_up=_carrier_up(link_state.source_carrier_state),
    )
    source_state, target_state = compute_link_oper_states(
        source=source_input,
        target=target_input,
        transport_state=transport,
    )

    now = datetime.now(timezone.utc)
    changed = False

    old_source_state = link_state.source_oper_state
    old_source_reason = link_state.source_oper_reason
    old_target_state = link_state.target_oper_state
    old_target_reason = link_state.target_oper_reason

    if (
        link_state.source_oper_state != source_state.oper_state
        or link_state.source_oper_reason != source_state.reason
    ):
        link_state.source_oper_state = source_state.oper_state
        link_state.source_oper_reason = source_state.reason
        link_state.source_last_change_at = now
        changed = True
        record_link_oper_transition(
            endpoint="source",
            old_state=old_source_state,
            new_state=source_state.oper_state,
            reason=source_state.reason,
            is_cross_host=bool(link_state.is_cross_host),
        )
        if old_source_state != source_state.oper_state or old_source_reason != source_state.reason:
            logger_payload = {
                "event": "link_oper_transition",
                "lab_id": link_state.lab_id,
                "link_name": link_state.link_name,
                "endpoint": "source",
                "old_state": old_source_state,
                "new_state": source_state.oper_state,
                "old_reason": old_source_reason,
                "new_reason": source_state.reason,
                "desired_state": link_state.desired_state,
                "actual_state": link_state.actual_state,
                "transport_state": transport,
                "is_cross_host": link_state.is_cross_host,
            }
            logger.info("Link operational transition", extra=logger_payload)

    if (
        link_state.target_oper_state != target_state.oper_state
        or link_state.target_oper_reason != target_state.reason
    ):
        link_state.target_oper_state = target_state.oper_state
        link_state.target_oper_reason = target_state.reason
        link_state.target_last_change_at = now
        changed = True
        record_link_oper_transition(
            endpoint="target",
            old_state=old_target_state,
            new_state=target_state.oper_state,
            reason=target_state.reason,
            is_cross_host=bool(link_state.is_cross_host),
        )
        if old_target_state != target_state.oper_state or old_target_reason != target_state.reason:
            logger_payload = {
                "event": "link_oper_transition",
                "lab_id": link_state.lab_id,
                "link_name": link_state.link_name,
                "endpoint": "target",
                "old_state": old_target_state,
                "new_state": target_state.oper_state,
                "old_reason": old_target_reason,
                "new_reason": target_state.reason,
                "desired_state": link_state.desired_state,
                "actual_state": link_state.actual_state,
                "transport_state": transport,
                "is_cross_host": link_state.is_cross_host,
            }
            logger.info("Link operational transition", extra=logger_payload)

    if changed:
        link_state.oper_epoch = (link_state.oper_epoch or 0) + 1

    return changed
