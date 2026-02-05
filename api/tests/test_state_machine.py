from __future__ import annotations

import pytest

import app.services.state_machine as state_machine
from app.state import (
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)


def test_node_state_machine_transitions() -> None:
    assert state_machine.NodeStateMachine.can_transition(
        NodeActualState.UNDEPLOYED, NodeActualState.PENDING
    )
    assert state_machine.NodeStateMachine.can_transition(
        NodeActualState.RUNNING, NodeActualState.STOPPING
    )
    assert not state_machine.NodeStateMachine.can_transition(
        NodeActualState.RUNNING, NodeActualState.STARTING
    )


def test_node_state_machine_desired_transitions() -> None:
    assert (
        state_machine.NodeStateMachine.get_transition_for_desired(
            NodeActualState.UNDEPLOYED, NodeDesiredState.RUNNING
        )
        == NodeActualState.PENDING
    )
    assert (
        state_machine.NodeStateMachine.get_transition_for_desired(
            NodeActualState.PENDING, NodeDesiredState.RUNNING
        )
        == NodeActualState.STARTING
    )
    assert (
        state_machine.NodeStateMachine.get_transition_for_desired(
            NodeActualState.RUNNING, NodeDesiredState.STOPPED
        )
        == NodeActualState.STOPPING
    )
    assert (
        state_machine.NodeStateMachine.get_transition_for_desired(
            NodeActualState.STOPPED, NodeDesiredState.STOPPED
        )
        is None
    )


def test_node_state_machine_enforcement() -> None:
    assert state_machine.NodeStateMachine.matches_desired(
        NodeActualState.RUNNING, NodeDesiredState.RUNNING
    )
    assert state_machine.NodeStateMachine.needs_enforcement(
        NodeActualState.STOPPED, NodeDesiredState.RUNNING
    )
    assert state_machine.NodeStateMachine.get_enforcement_action(
        NodeActualState.STOPPED, NodeDesiredState.RUNNING
    ) == "start"
    assert state_machine.NodeStateMachine.get_enforcement_action(
        NodeActualState.RUNNING, NodeDesiredState.STOPPED
    ) == "stop"


def test_link_state_machine() -> None:
    assert state_machine.LinkStateMachine.can_transition(
        LinkActualState.UNKNOWN, LinkActualState.PENDING
    )
    assert state_machine.LinkStateMachine.matches_desired(
        LinkActualState.UP, LinkDesiredState.UP
    )
    assert not state_machine.LinkStateMachine.matches_desired(
        LinkActualState.DOWN, LinkDesiredState.UP
    )
    assert state_machine.LinkStateMachine.should_auto_connect(
        LinkActualState.UNKNOWN,
        LinkDesiredState.UP,
        source_node_running=True,
        target_node_running=True,
    )
    assert not state_machine.LinkStateMachine.should_auto_connect(
        LinkActualState.UP,
        LinkDesiredState.UP,
        source_node_running=True,
        target_node_running=True,
    )
