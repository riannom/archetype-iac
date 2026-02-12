from __future__ import annotations

import pytest

from app.services.link_operational_state import (
    OPER_DOWN,
    OPER_UP,
    REASON_ADMIN_DOWN,
    REASON_LOCAL_INTERFACE_DOWN,
    REASON_LOCAL_NODE_DOWN,
    REASON_PEER_HOST_OFFLINE,
    REASON_PEER_INTERFACE_DOWN,
    REASON_PEER_NODE_DOWN,
    REASON_TRANSPORT_DEGRADED,
    REASON_TRANSPORT_DOWN,
    REASON_UNKNOWN,
    TRANSPORT_DEGRADED,
    TRANSPORT_DOWN,
    TRANSPORT_UP,
    EndpointOperationalInput,
    compute_endpoint_oper_state,
    compute_link_oper_states,
)


def _healthy_endpoint(*, admin_state: str = "up") -> EndpointOperationalInput:
    return EndpointOperationalInput(
        admin_state=admin_state,
        local_node_running=True,
        local_interface_up=True,
        peer_host_online=True,
        peer_node_running=True,
        peer_interface_up=True,
    )


@pytest.mark.parametrize(
    ("endpoint", "transport_state", "expected_state", "expected_reason"),
    [
        (_healthy_endpoint(admin_state="down"), TRANSPORT_UP, OPER_DOWN, REASON_ADMIN_DOWN),
        (
            EndpointOperationalInput(
                admin_state="up",
                local_node_running=False,
                local_interface_up=True,
                peer_host_online=True,
                peer_node_running=True,
                peer_interface_up=True,
            ),
            TRANSPORT_UP,
            OPER_DOWN,
            REASON_LOCAL_NODE_DOWN,
        ),
        (
            EndpointOperationalInput(
                admin_state="up",
                local_node_running=True,
                local_interface_up=False,
                peer_host_online=True,
                peer_node_running=True,
                peer_interface_up=True,
            ),
            TRANSPORT_UP,
            OPER_DOWN,
            REASON_LOCAL_INTERFACE_DOWN,
        ),
        (
            EndpointOperationalInput(
                admin_state="up",
                local_node_running=True,
                local_interface_up=True,
                peer_host_online=False,
                peer_node_running=True,
                peer_interface_up=True,
            ),
            TRANSPORT_UP,
            OPER_DOWN,
            REASON_PEER_HOST_OFFLINE,
        ),
        (
            EndpointOperationalInput(
                admin_state="up",
                local_node_running=True,
                local_interface_up=True,
                peer_host_online=True,
                peer_node_running=False,
                peer_interface_up=True,
            ),
            TRANSPORT_UP,
            OPER_DOWN,
            REASON_PEER_NODE_DOWN,
        ),
        (
            EndpointOperationalInput(
                admin_state="up",
                local_node_running=True,
                local_interface_up=True,
                peer_host_online=True,
                peer_node_running=True,
                peer_interface_up=False,
            ),
            TRANSPORT_UP,
            OPER_DOWN,
            REASON_PEER_INTERFACE_DOWN,
        ),
        (_healthy_endpoint(), TRANSPORT_DOWN, OPER_DOWN, REASON_TRANSPORT_DOWN),
        (
            _healthy_endpoint(),
            TRANSPORT_DEGRADED,
            OPER_DOWN,
            REASON_TRANSPORT_DEGRADED,
        ),
        (_healthy_endpoint(), "weird", OPER_DOWN, REASON_UNKNOWN),
        (_healthy_endpoint(), TRANSPORT_UP, OPER_UP, None),
    ],
)
def test_compute_endpoint_oper_state_truth_table(
    endpoint: EndpointOperationalInput,
    transport_state: str,
    expected_state: str,
    expected_reason: str | None,
) -> None:
    result = compute_endpoint_oper_state(endpoint, transport_state)
    assert result.oper_state == expected_state
    assert result.reason == expected_reason


def test_compute_link_oper_states_independent_per_endpoint() -> None:
    source = EndpointOperationalInput(
        admin_state="up",
        local_node_running=True,
        local_interface_up=True,
        peer_host_online=True,
        peer_node_running=True,
        peer_interface_up=True,
    )
    target = EndpointOperationalInput(
        admin_state="up",
        local_node_running=True,
        local_interface_up=False,
        peer_host_online=True,
        peer_node_running=True,
        peer_interface_up=True,
    )
    source_state, target_state = compute_link_oper_states(
        source,
        target,
        TRANSPORT_UP,
    )
    assert source_state.oper_state == OPER_UP
    assert source_state.reason is None
    assert target_state.oper_state == OPER_DOWN
    assert target_state.reason == REASON_LOCAL_INTERFACE_DOWN


def test_compute_endpoint_oper_state_exhaustive_prerequisite_invariant() -> None:
    """If any prerequisite is false, endpoint must be oper-down."""
    bools = [False, True]
    for local_node_running in bools:
        for local_interface_up in bools:
            for peer_host_online in bools:
                for peer_node_running in bools:
                    for peer_interface_up in bools:
                        endpoint = EndpointOperationalInput(
                            admin_state="up",
                            local_node_running=local_node_running,
                            local_interface_up=local_interface_up,
                            peer_host_online=peer_host_online,
                            peer_node_running=peer_node_running,
                            peer_interface_up=peer_interface_up,
                        )
                        result = compute_endpoint_oper_state(endpoint, TRANSPORT_UP)
                        all_ok = (
                            local_node_running
                            and local_interface_up
                            and peer_host_online
                            and peer_node_running
                            and peer_interface_up
                        )
                        if all_ok:
                            assert result.oper_state == OPER_UP
                            assert result.reason is None
                        else:
                            assert result.oper_state == OPER_DOWN
                            assert result.reason is not None


def test_compute_link_oper_states_symmetry_when_inputs_mirrored() -> None:
    """Mirrored endpoint inputs should produce mirrored oper states."""
    source = EndpointOperationalInput(
        admin_state="up",
        local_node_running=True,
        local_interface_up=True,
        peer_host_online=False,
        peer_node_running=True,
        peer_interface_up=True,
    )
    target = EndpointOperationalInput(
        admin_state="up",
        local_node_running=True,
        local_interface_up=True,
        peer_host_online=False,
        peer_node_running=True,
        peer_interface_up=True,
    )
    source_state, target_state = compute_link_oper_states(source, target, TRANSPORT_UP)
    assert source_state.oper_state == target_state.oper_state
    assert source_state.reason == target_state.reason
