"""Exhaustive state machine tests (Phase 0.1).

Tests ALL valid transitions, invalid transitions, state sets, enforcement logic,
desired-state transitions, and lab state aggregation.
"""
from __future__ import annotations

import pytest

from app.services.state_machine import LabStateMachine, LinkStateMachine, NodeStateMachine
from app.state import (
    LabState,
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)


# ---------------------------------------------------------------------------
# NodeStateMachine — Valid Transitions (parametrized over every edge)
# ---------------------------------------------------------------------------

# Build the full list of (current, target) pairs from the VALID_TRANSITIONS map
_VALID_NODE_TRANSITIONS: list[tuple[NodeActualState, NodeActualState]] = []
for _src, _dsts in NodeStateMachine.VALID_TRANSITIONS.items():
    for _dst in _dsts:
        _VALID_NODE_TRANSITIONS.append((_src, _dst))


@pytest.mark.parametrize("current,target", _VALID_NODE_TRANSITIONS,
                         ids=[f"{c.value}->{t.value}" for c, t in _VALID_NODE_TRANSITIONS])
def test_valid_node_transition(current: NodeActualState, target: NodeActualState) -> None:
    """Every edge in VALID_TRANSITIONS must be accepted by can_transition."""
    assert NodeStateMachine.can_transition(current, target)


# ---------------------------------------------------------------------------
# NodeStateMachine — Self-transitions always valid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", list(NodeActualState),
                         ids=[s.value for s in NodeActualState])
def test_self_transition_always_valid(state: NodeActualState) -> None:
    """A node can always transition to its own state (no-op)."""
    assert NodeStateMachine.can_transition(state, state)


# ---------------------------------------------------------------------------
# NodeStateMachine — Invalid Transitions (parametrized sample of key illegal edges)
# ---------------------------------------------------------------------------

_INVALID_NODE_TRANSITIONS = [
    # Can't skip to running from undeployed (must go through pending)
    (NodeActualState.UNDEPLOYED, NodeActualState.RUNNING),
    (NodeActualState.UNDEPLOYED, NodeActualState.STARTING),
    (NodeActualState.UNDEPLOYED, NodeActualState.STOPPING),
    (NodeActualState.UNDEPLOYED, NodeActualState.STOPPED),
    # Can't go backwards from running to pending/starting
    (NodeActualState.RUNNING, NodeActualState.STARTING),
    (NodeActualState.RUNNING, NodeActualState.PENDING),
    (NodeActualState.RUNNING, NodeActualState.UNDEPLOYED),
    # Can't go from stopping to running or starting
    (NodeActualState.STOPPING, NodeActualState.RUNNING),
    (NodeActualState.STOPPING, NodeActualState.STARTING),
    (NodeActualState.STOPPING, NodeActualState.PENDING),
    (NodeActualState.STOPPING, NodeActualState.UNDEPLOYED),
    # Can't go from starting to undeployed or pending
    (NodeActualState.STARTING, NodeActualState.PENDING),
    (NodeActualState.STARTING, NodeActualState.UNDEPLOYED),
    (NodeActualState.STARTING, NodeActualState.STOPPING),
    # Can't go from pending to stopped directly
    (NodeActualState.PENDING, NodeActualState.STOPPED),
    (NodeActualState.PENDING, NodeActualState.STOPPING),
    # Error can't go directly to running or stopping/starting via VALID_TRANSITIONS
    # (ERROR -> {PENDING, STARTING, STOPPED, UNDEPLOYED})
    (NodeActualState.ERROR, NodeActualState.RUNNING),
    (NodeActualState.ERROR, NodeActualState.STOPPING),
    (NodeActualState.ERROR, NodeActualState.EXITED),
]


@pytest.mark.parametrize("current,target", _INVALID_NODE_TRANSITIONS,
                         ids=[f"{c.value}->{t.value}" for c, t in _INVALID_NODE_TRANSITIONS])
def test_invalid_node_transition(current: NodeActualState, target: NodeActualState) -> None:
    """Key illegal transitions must be rejected by can_transition."""
    assert not NodeStateMachine.can_transition(current, target)


# ---------------------------------------------------------------------------
# NodeStateMachine — EXITED state coverage (previously zero coverage)
# ---------------------------------------------------------------------------

class TestExitedState:
    """EXITED state had zero test coverage. Verify all its transitions."""

    def test_exited_can_go_to_starting(self) -> None:
        assert NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.STARTING)

    def test_exited_can_go_to_pending(self) -> None:
        assert NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.PENDING)

    def test_exited_can_go_to_stopped(self) -> None:
        assert NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.STOPPED)

    def test_exited_can_go_to_error(self) -> None:
        assert NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.ERROR)

    def test_exited_cannot_go_to_running(self) -> None:
        assert not NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.RUNNING)

    def test_exited_cannot_go_to_stopping(self) -> None:
        assert not NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.STOPPING)

    def test_exited_cannot_go_to_undeployed(self) -> None:
        assert not NodeStateMachine.can_transition(NodeActualState.EXITED, NodeActualState.UNDEPLOYED)

    def test_exited_is_not_terminal(self) -> None:
        """EXITED is NOT terminal — it needs resolution (restart or mark stopped)."""
        assert not NodeStateMachine.is_terminal(NodeActualState.EXITED)

    def test_exited_is_container_exists(self) -> None:
        """Container still exists in EXITED state."""
        assert NodeActualState.EXITED in NodeStateMachine.CONTAINER_EXISTS_STATES

    def test_exited_is_stopped_equivalent(self) -> None:
        """EXITED is treated as stopped for enforcement purposes."""
        assert NodeActualState.EXITED in NodeStateMachine.STOPPED_EQUIVALENT_STATES

    def test_exited_matches_desired_stopped(self) -> None:
        """EXITED matches desired=stopped (it's a stopped-equivalent)."""
        assert NodeStateMachine.matches_desired(NodeActualState.EXITED, NodeDesiredState.STOPPED)

    def test_exited_does_not_match_desired_running(self) -> None:
        """EXITED does NOT match desired=running."""
        assert not NodeStateMachine.matches_desired(NodeActualState.EXITED, NodeDesiredState.RUNNING)

    def test_exited_needs_enforcement_when_desired_running(self) -> None:
        """EXITED with desired=running needs enforcement (start)."""
        assert NodeStateMachine.needs_enforcement(NodeActualState.EXITED, NodeDesiredState.RUNNING)

    def test_exited_enforcement_action_is_start(self) -> None:
        """Enforcement action for EXITED with desired=running is 'start'."""
        assert NodeStateMachine.get_enforcement_action(
            NodeActualState.EXITED, NodeDesiredState.RUNNING
        ) == "start"


# ---------------------------------------------------------------------------
# NodeStateMachine — is_terminal
# ---------------------------------------------------------------------------

_TERMINAL = [NodeActualState.RUNNING, NodeActualState.STOPPED, NodeActualState.ERROR, NodeActualState.UNDEPLOYED]
_NON_TERMINAL = [NodeActualState.PENDING, NodeActualState.STARTING, NodeActualState.STOPPING, NodeActualState.EXITED]


@pytest.mark.parametrize("state", _TERMINAL, ids=[s.value for s in _TERMINAL])
def test_terminal_states(state: NodeActualState) -> None:
    assert NodeStateMachine.is_terminal(state)


@pytest.mark.parametrize("state", _NON_TERMINAL, ids=[s.value for s in _NON_TERMINAL])
def test_non_terminal_states(state: NodeActualState) -> None:
    assert not NodeStateMachine.is_terminal(state)


# ---------------------------------------------------------------------------
# NodeStateMachine — CONTAINER_EXISTS_STATES
# ---------------------------------------------------------------------------

_CONTAINER_EXISTS = [
    NodeActualState.RUNNING, NodeActualState.STOPPED, NodeActualState.STOPPING,
    NodeActualState.STARTING, NodeActualState.EXITED, NodeActualState.ERROR,
]
_CONTAINER_NOT_EXISTS = [NodeActualState.UNDEPLOYED, NodeActualState.PENDING]


@pytest.mark.parametrize("state", _CONTAINER_EXISTS, ids=[s.value for s in _CONTAINER_EXISTS])
def test_container_exists_states(state: NodeActualState) -> None:
    assert state in NodeStateMachine.CONTAINER_EXISTS_STATES


@pytest.mark.parametrize("state", _CONTAINER_NOT_EXISTS, ids=[s.value for s in _CONTAINER_NOT_EXISTS])
def test_container_not_exists_states(state: NodeActualState) -> None:
    assert state not in NodeStateMachine.CONTAINER_EXISTS_STATES


# ---------------------------------------------------------------------------
# NodeStateMachine — STOPPED_EQUIVALENT_STATES
# ---------------------------------------------------------------------------

_STOPPED_EQUIV = [NodeActualState.STOPPED, NodeActualState.EXITED, NodeActualState.UNDEPLOYED, NodeActualState.PENDING]
_NOT_STOPPED_EQUIV = [NodeActualState.RUNNING, NodeActualState.STARTING, NodeActualState.STOPPING, NodeActualState.ERROR]


@pytest.mark.parametrize("state", _STOPPED_EQUIV, ids=[s.value for s in _STOPPED_EQUIV])
def test_stopped_equivalent_states(state: NodeActualState) -> None:
    assert state in NodeStateMachine.STOPPED_EQUIVALENT_STATES


@pytest.mark.parametrize("state", _NOT_STOPPED_EQUIV, ids=[s.value for s in _NOT_STOPPED_EQUIV])
def test_not_stopped_equivalent_states(state: NodeActualState) -> None:
    assert state not in NodeStateMachine.STOPPED_EQUIVALENT_STATES


# ---------------------------------------------------------------------------
# NodeStateMachine — matches_desired
# ---------------------------------------------------------------------------

class TestMatchesDesired:
    def test_running_matches_running(self) -> None:
        assert NodeStateMachine.matches_desired(NodeActualState.RUNNING, NodeDesiredState.RUNNING)

    def test_stopped_matches_stopped(self) -> None:
        assert NodeStateMachine.matches_desired(NodeActualState.STOPPED, NodeDesiredState.STOPPED)

    def test_undeployed_matches_stopped(self) -> None:
        """Undeployed is a stopped-equivalent — matches desired=stopped."""
        assert NodeStateMachine.matches_desired(NodeActualState.UNDEPLOYED, NodeDesiredState.STOPPED)

    def test_exited_matches_stopped(self) -> None:
        assert NodeStateMachine.matches_desired(NodeActualState.EXITED, NodeDesiredState.STOPPED)

    def test_pending_matches_stopped(self) -> None:
        assert NodeStateMachine.matches_desired(NodeActualState.PENDING, NodeDesiredState.STOPPED)

    def test_running_does_not_match_stopped(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.RUNNING, NodeDesiredState.STOPPED)

    def test_stopped_does_not_match_running(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.STOPPED, NodeDesiredState.RUNNING)

    def test_error_does_not_match_running(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.ERROR, NodeDesiredState.RUNNING)

    def test_error_does_not_match_stopped(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.ERROR, NodeDesiredState.STOPPED)

    def test_starting_does_not_match_running(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.STARTING, NodeDesiredState.RUNNING)

    def test_stopping_does_not_match_stopped(self) -> None:
        assert not NodeStateMachine.matches_desired(NodeActualState.STOPPING, NodeDesiredState.STOPPED)


# ---------------------------------------------------------------------------
# NodeStateMachine — get_transition_for_desired
# ---------------------------------------------------------------------------

class TestGetTransitionForDesired:
    """Test the computed next-state for every (actual, desired) combination."""

    # --- desired = RUNNING ---
    def test_undeployed_to_running(self) -> None:
        """Undeployed -> pending (first deploy)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.UNDEPLOYED, NodeDesiredState.RUNNING
        ) == NodeActualState.PENDING

    def test_pending_to_running(self) -> None:
        """Pending -> starting (retry deploy)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.PENDING, NodeDesiredState.RUNNING
        ) == NodeActualState.STARTING

    def test_stopped_to_running(self) -> None:
        """Stopped -> starting."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STOPPED, NodeDesiredState.RUNNING
        ) == NodeActualState.STARTING

    def test_exited_to_running(self) -> None:
        """Exited -> starting (same as stopped)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.EXITED, NodeDesiredState.RUNNING
        ) == NodeActualState.STARTING

    def test_error_to_running(self) -> None:
        """Error -> pending (retry via full deploy path)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.ERROR, NodeDesiredState.RUNNING
        ) == NodeActualState.PENDING

    def test_running_to_running(self) -> None:
        """Already running — no transition needed."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.RUNNING, NodeDesiredState.RUNNING
        ) is None

    def test_starting_to_running(self) -> None:
        """Already starting — no transition needed (in progress)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STARTING, NodeDesiredState.RUNNING
        ) is None

    def test_stopping_to_running(self) -> None:
        """Stopping with desired=running — no transition (let stop complete first)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STOPPING, NodeDesiredState.RUNNING
        ) is None

    # --- desired = STOPPED ---
    def test_running_to_stopped(self) -> None:
        """Running -> stopping."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.RUNNING, NodeDesiredState.STOPPED
        ) == NodeActualState.STOPPING

    def test_stopped_to_stopped(self) -> None:
        """Already stopped — no transition needed."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STOPPED, NodeDesiredState.STOPPED
        ) is None

    def test_undeployed_to_stopped(self) -> None:
        """Already undeployed — no transition needed (stopped-equivalent)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.UNDEPLOYED, NodeDesiredState.STOPPED
        ) is None

    def test_pending_to_stopped(self) -> None:
        """Pending -> undeployed (abort pending deploy)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.PENDING, NodeDesiredState.STOPPED
        ) == NodeActualState.UNDEPLOYED

    def test_starting_to_stopped(self) -> None:
        """Starting with desired=stopped — no transition (let start finish)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STARTING, NodeDesiredState.STOPPED
        ) is None

    def test_stopping_to_stopped(self) -> None:
        """Already stopping — no transition needed."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.STOPPING, NodeDesiredState.STOPPED
        ) is None

    def test_error_to_stopped(self) -> None:
        """Error with desired=stopped — no transition (error is not running)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.ERROR, NodeDesiredState.STOPPED
        ) is None

    def test_exited_to_stopped(self) -> None:
        """Exited with desired=stopped — no transition (exited is stopped-equivalent)."""
        assert NodeStateMachine.get_transition_for_desired(
            NodeActualState.EXITED, NodeDesiredState.STOPPED
        ) is None


# ---------------------------------------------------------------------------
# NodeStateMachine — needs_enforcement
# ---------------------------------------------------------------------------

class TestNeedsEnforcement:
    """Verify enforcement logic for all state/desired combinations."""

    # States that need enforcement when desired=running
    @pytest.mark.parametrize("actual", [
        NodeActualState.STOPPED,
        NodeActualState.EXITED,
        NodeActualState.UNDEPLOYED,
        NodeActualState.ERROR,
    ], ids=lambda s: s.value)
    def test_needs_enforcement_for_running(self, actual: NodeActualState) -> None:
        assert NodeStateMachine.needs_enforcement(actual, NodeDesiredState.RUNNING)

    # Running with desired=stopped needs enforcement
    def test_running_needs_stop_enforcement(self) -> None:
        assert NodeStateMachine.needs_enforcement(NodeActualState.RUNNING, NodeDesiredState.STOPPED)

    # Transitional states NEVER need enforcement (avoid races)
    @pytest.mark.parametrize("actual", [
        NodeActualState.PENDING,
        NodeActualState.STARTING,
        NodeActualState.STOPPING,
    ], ids=lambda s: s.value)
    def test_transitional_states_skip_enforcement(self, actual: NodeActualState) -> None:
        """Transitional states must never trigger enforcement to avoid race conditions."""
        assert not NodeStateMachine.needs_enforcement(actual, NodeDesiredState.RUNNING)
        assert not NodeStateMachine.needs_enforcement(actual, NodeDesiredState.STOPPED)

    # Already in desired state — no enforcement
    def test_running_desired_running_no_enforcement(self) -> None:
        assert not NodeStateMachine.needs_enforcement(NodeActualState.RUNNING, NodeDesiredState.RUNNING)

    def test_stopped_desired_stopped_no_enforcement(self) -> None:
        assert not NodeStateMachine.needs_enforcement(NodeActualState.STOPPED, NodeDesiredState.STOPPED)

    def test_undeployed_desired_stopped_no_enforcement(self) -> None:
        assert not NodeStateMachine.needs_enforcement(NodeActualState.UNDEPLOYED, NodeDesiredState.STOPPED)


# ---------------------------------------------------------------------------
# NodeStateMachine — get_enforcement_action
# ---------------------------------------------------------------------------

class TestGetEnforcementAction:
    """Verify the correct action string for all enforceable combinations."""

    # Start actions
    @pytest.mark.parametrize("actual", [
        NodeActualState.STOPPED,
        NodeActualState.EXITED,
        NodeActualState.UNDEPLOYED,
        NodeActualState.ERROR,
    ], ids=lambda s: s.value)
    def test_enforcement_start(self, actual: NodeActualState) -> None:
        assert NodeStateMachine.get_enforcement_action(actual, NodeDesiredState.RUNNING) == "start"

    # Stop action
    def test_enforcement_stop(self) -> None:
        assert NodeStateMachine.get_enforcement_action(
            NodeActualState.RUNNING, NodeDesiredState.STOPPED
        ) == "stop"

    # No action for already-matching states
    def test_no_action_running_running(self) -> None:
        assert NodeStateMachine.get_enforcement_action(
            NodeActualState.RUNNING, NodeDesiredState.RUNNING
        ) is None

    def test_no_action_stopped_stopped(self) -> None:
        assert NodeStateMachine.get_enforcement_action(
            NodeActualState.STOPPED, NodeDesiredState.STOPPED
        ) is None

    # No action for transitional states
    @pytest.mark.parametrize("actual", [
        NodeActualState.PENDING,
        NodeActualState.STARTING,
        NodeActualState.STOPPING,
    ], ids=lambda s: s.value)
    def test_no_action_transitional(self, actual: NodeActualState) -> None:
        assert NodeStateMachine.get_enforcement_action(actual, NodeDesiredState.RUNNING) is None
        assert NodeStateMachine.get_enforcement_action(actual, NodeDesiredState.STOPPED) is None


# ---------------------------------------------------------------------------
# LinkStateMachine — Valid Transitions (parametrized)
# ---------------------------------------------------------------------------

_VALID_LINK_TRANSITIONS: list[tuple[LinkActualState, LinkActualState]] = []
for _src, _dsts in LinkStateMachine.VALID_TRANSITIONS.items():
    for _dst in _dsts:
        _VALID_LINK_TRANSITIONS.append((_src, _dst))


@pytest.mark.parametrize("current,target", _VALID_LINK_TRANSITIONS,
                         ids=[f"{c.value}->{t.value}" for c, t in _VALID_LINK_TRANSITIONS])
def test_valid_link_transition(current: LinkActualState, target: LinkActualState) -> None:
    assert LinkStateMachine.can_transition(current, target)


# Link self-transitions
@pytest.mark.parametrize("state", list(LinkActualState), ids=[s.value for s in LinkActualState])
def test_link_self_transition(state: LinkActualState) -> None:
    assert LinkStateMachine.can_transition(state, state)


# Key invalid link transitions
_INVALID_LINK_TRANSITIONS = [
    (LinkActualState.UP, LinkActualState.PENDING),
    (LinkActualState.UP, LinkActualState.CREATING),
    (LinkActualState.UP, LinkActualState.UNKNOWN),
    (LinkActualState.CREATING, LinkActualState.PENDING),
    (LinkActualState.CREATING, LinkActualState.UNKNOWN),
]


@pytest.mark.parametrize("current,target", _INVALID_LINK_TRANSITIONS,
                         ids=[f"{c.value}->{t.value}" for c, t in _INVALID_LINK_TRANSITIONS])
def test_invalid_link_transition(current: LinkActualState, target: LinkActualState) -> None:
    assert not LinkStateMachine.can_transition(current, target)


# ---------------------------------------------------------------------------
# LinkStateMachine — matches_desired
# ---------------------------------------------------------------------------

class TestLinkMatchesDesired:
    def test_up_matches_up(self) -> None:
        assert LinkStateMachine.matches_desired(LinkActualState.UP, LinkDesiredState.UP)

    def test_down_matches_down(self) -> None:
        assert LinkStateMachine.matches_desired(LinkActualState.DOWN, LinkDesiredState.DOWN)

    def test_up_does_not_match_down(self) -> None:
        assert not LinkStateMachine.matches_desired(LinkActualState.UP, LinkDesiredState.DOWN)

    def test_down_does_not_match_up(self) -> None:
        assert not LinkStateMachine.matches_desired(LinkActualState.DOWN, LinkDesiredState.UP)

    @pytest.mark.parametrize("actual", [
        LinkActualState.UNKNOWN, LinkActualState.PENDING,
        LinkActualState.CREATING, LinkActualState.ERROR,
    ], ids=lambda s: s.value)
    def test_transitional_matches_neither(self, actual: LinkActualState) -> None:
        assert not LinkStateMachine.matches_desired(actual, LinkDesiredState.UP)
        assert not LinkStateMachine.matches_desired(actual, LinkDesiredState.DOWN)


# ---------------------------------------------------------------------------
# LinkStateMachine — should_auto_connect
# ---------------------------------------------------------------------------

class TestShouldAutoConnect:
    """Auto-connect requires: desired=UP, both nodes running, connectable state."""

    @pytest.mark.parametrize("actual", list(LinkStateMachine.CONNECTABLE_STATES),
                             ids=lambda s: s.value)
    def test_auto_connect_when_connectable(self, actual: LinkActualState) -> None:
        assert LinkStateMachine.should_auto_connect(
            actual, LinkDesiredState.UP, source_node_running=True, target_node_running=True,
        )

    def test_no_auto_connect_when_already_up(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.UP, LinkDesiredState.UP,
            source_node_running=True, target_node_running=True,
        )

    def test_no_auto_connect_when_creating(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.CREATING, LinkDesiredState.UP,
            source_node_running=True, target_node_running=True,
        )

    def test_no_auto_connect_when_desired_down(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.UNKNOWN, LinkDesiredState.DOWN,
            source_node_running=True, target_node_running=True,
        )

    def test_no_auto_connect_when_source_not_running(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.UNKNOWN, LinkDesiredState.UP,
            source_node_running=False, target_node_running=True,
        )

    def test_no_auto_connect_when_target_not_running(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.UNKNOWN, LinkDesiredState.UP,
            source_node_running=True, target_node_running=False,
        )

    def test_no_auto_connect_when_both_not_running(self) -> None:
        assert not LinkStateMachine.should_auto_connect(
            LinkActualState.UNKNOWN, LinkDesiredState.UP,
            source_node_running=False, target_node_running=False,
        )


# ---------------------------------------------------------------------------
# LabStateMachine — compute_lab_state (all combinations)
# ---------------------------------------------------------------------------

class TestComputeLabState:
    """Test lab state aggregation from node state counts."""

    def test_all_running(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=5, stopped_count=0, undeployed_count=0, error_count=0) == LabState.RUNNING

    def test_all_stopped(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=5, undeployed_count=0, error_count=0) == LabState.STOPPED

    def test_all_undeployed(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=0, undeployed_count=5, error_count=0) == LabState.STOPPED

    def test_mixed_stopped_and_undeployed(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=2, undeployed_count=3, error_count=0) == LabState.STOPPED

    def test_any_error(self) -> None:
        """Error takes priority over everything."""
        assert LabStateMachine.compute_lab_state(running_count=4, stopped_count=0, undeployed_count=0, error_count=1) == LabState.ERROR

    def test_error_only(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=0, undeployed_count=0, error_count=3) == LabState.ERROR

    def test_error_with_stopped(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=3, undeployed_count=0, error_count=1) == LabState.ERROR

    def test_any_stopping(self) -> None:
        """Stopping takes priority over terminal states (but not error)."""
        assert LabStateMachine.compute_lab_state(
            running_count=3, stopped_count=0, undeployed_count=0, error_count=0, stopping_count=1
        ) == LabState.STOPPING

    def test_any_starting(self) -> None:
        """Starting takes priority over terminal states (but not error/stopping)."""
        assert LabStateMachine.compute_lab_state(
            running_count=3, stopped_count=0, undeployed_count=0, error_count=0, starting_count=1
        ) == LabState.STARTING

    def test_any_pending(self) -> None:
        """Pending is treated as starting."""
        assert LabStateMachine.compute_lab_state(
            running_count=0, stopped_count=3, undeployed_count=0, error_count=0, pending_count=1
        ) == LabState.STARTING

    def test_stopping_trumps_starting(self) -> None:
        """If both stopping and starting nodes exist, stopping wins."""
        assert LabStateMachine.compute_lab_state(
            running_count=0, stopped_count=0, undeployed_count=0, error_count=0,
            starting_count=1, stopping_count=1,
        ) == LabState.STOPPING

    def test_error_trumps_transitional(self) -> None:
        """Error takes priority over transitional states."""
        assert LabStateMachine.compute_lab_state(
            running_count=0, stopped_count=0, undeployed_count=0, error_count=1,
            starting_count=2, stopping_count=1,
        ) == LabState.ERROR

    def test_mixed_running_and_stopped(self) -> None:
        """Mixed running/stopped -> running (partial is valid)."""
        assert LabStateMachine.compute_lab_state(running_count=3, stopped_count=2, undeployed_count=0, error_count=0) == LabState.RUNNING

    def test_mixed_running_and_undeployed(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=3, stopped_count=0, undeployed_count=2, error_count=0) == LabState.RUNNING

    def test_empty_lab(self) -> None:
        """Empty lab (no nodes) -> stopped."""
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=0, undeployed_count=0, error_count=0) == LabState.STOPPED

    def test_single_running(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=1, stopped_count=0, undeployed_count=0, error_count=0) == LabState.RUNNING

    def test_single_stopped(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=1, undeployed_count=0, error_count=0) == LabState.STOPPED

    def test_single_error(self) -> None:
        assert LabStateMachine.compute_lab_state(running_count=0, stopped_count=0, undeployed_count=0, error_count=1) == LabState.ERROR


# ---------------------------------------------------------------------------
# LabStateMachine — is_transitional
# ---------------------------------------------------------------------------

class TestLabIsTransitional:
    @pytest.mark.parametrize("state", [LabState.STARTING, LabState.STOPPING, LabState.UNKNOWN],
                             ids=lambda s: s.value)
    def test_transitional_states(self, state: LabState) -> None:
        assert LabStateMachine.is_transitional(state)

    @pytest.mark.parametrize("state", [LabState.RUNNING, LabState.STOPPED, LabState.ERROR],
                             ids=lambda s: s.value)
    def test_non_transitional_states(self, state: LabState) -> None:
        assert not LabStateMachine.is_transitional(state)


# ---------------------------------------------------------------------------
# State Enum Consistency — every NodeActualState is in VALID_TRANSITIONS
# ---------------------------------------------------------------------------

def test_all_node_states_have_transition_rules() -> None:
    """Every NodeActualState must have an entry in VALID_TRANSITIONS."""
    for state in NodeActualState:
        assert state in NodeStateMachine.VALID_TRANSITIONS, (
            f"{state.value} missing from VALID_TRANSITIONS"
        )


def test_all_link_states_have_transition_rules() -> None:
    """Every LinkActualState must have an entry in VALID_TRANSITIONS."""
    for state in LinkActualState:
        assert state in LinkStateMachine.VALID_TRANSITIONS, (
            f"{state.value} missing from VALID_TRANSITIONS"
        )


def test_all_valid_transition_targets_are_valid_states() -> None:
    """Every target in VALID_TRANSITIONS must be a valid NodeActualState."""
    for src, targets in NodeStateMachine.VALID_TRANSITIONS.items():
        for target in targets:
            assert isinstance(target, NodeActualState), (
                f"Invalid target {target} from {src.value}"
            )


# ---------------------------------------------------------------------------
# NodeStateMachine — can_accept_command (Phase 6.1)
# ---------------------------------------------------------------------------

class TestCanAcceptCommand:
    """Test centralized command guard for single-node operations."""

    # Blocked combinations
    def test_start_blocked_while_stopping(self) -> None:
        allowed, reason = NodeStateMachine.can_accept_command("stopping", "start")
        assert not allowed
        assert "stopping" in reason

    def test_stop_allowed_while_starting(self) -> None:
        """Stop should be allowed for starting nodes (VMs can take minutes to boot)."""
        allowed, reason = NodeStateMachine.can_accept_command("starting", "stop")
        assert allowed

    # Allowed combinations — all 8 states × 2 commands except start-while-stopping
    @pytest.mark.parametrize("actual,cmd", [
        ("undeployed", "start"), ("undeployed", "stop"),
        ("pending", "start"), ("pending", "stop"),
        ("starting", "start"),  # starting + start = OK (already starting)
        ("starting", "stop"),   # starting + stop = OK (abort slow boot)
        ("running", "start"), ("running", "stop"),
        ("stopping", "stop"),  # stopping + stop = OK (already stopping)
        ("stopped", "start"), ("stopped", "stop"),
        ("exited", "start"), ("exited", "stop"),
        ("error", "start"), ("error", "stop"),
    ])
    def test_allowed_combinations(self, actual: str, cmd: str) -> None:
        allowed, reason = NodeStateMachine.can_accept_command(actual, cmd)
        assert allowed
        assert reason == ""


# ---------------------------------------------------------------------------
# NodeStateMachine — can_accept_bulk_command (Phase 6.1)
# ---------------------------------------------------------------------------

class TestCanAcceptBulkCommand:
    """Test centralized bulk command classification."""

    # Transitional states always skip
    @pytest.mark.parametrize("actual", ["starting", "stopping", "pending"])
    def test_skip_transitional(self, actual: str) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command(actual, "stopped", "start")
        assert classification == "skip_transitional"

    def test_stop_starting_node_proceeds(self) -> None:
        """Stopping a starting node should proceed (VMs can take minutes to boot)."""
        classification, _ = NodeStateMachine.can_accept_bulk_command("starting", "running", "stop")
        assert classification == "proceed"

    # Already in state
    def test_already_running(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("running", "running", "start")
        assert classification == "already_in_state"

    def test_already_stopped(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("stopped", "stopped", "stop")
        assert classification == "already_in_state"

    def test_already_undeployed_stopped(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("undeployed", "stopped", "stop")
        assert classification == "already_in_state"

    # Error retry
    def test_error_retry_start(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("error", "running", "start")
        assert classification == "reset_and_proceed"

    def test_error_stop_is_proceed(self) -> None:
        """Stopping an error node is a normal proceed (not reset)."""
        classification, _ = NodeStateMachine.can_accept_bulk_command("error", "running", "stop")
        assert classification == "proceed"

    # Normal proceed
    def test_stopped_start_proceed(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("stopped", "stopped", "start")
        assert classification == "proceed"

    def test_running_stop_proceed(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("running", "running", "stop")
        assert classification == "proceed"

    def test_exited_start_proceed(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("exited", "stopped", "start")
        assert classification == "proceed"

    def test_undeployed_start_proceed(self) -> None:
        classification, _ = NodeStateMachine.can_accept_bulk_command("undeployed", "stopped", "start")
        assert classification == "proceed"


# ---------------------------------------------------------------------------
# NodeStateMachine — needs_sync (Phase 6.1)
# ---------------------------------------------------------------------------

class TestNeedsSync:
    """Test centralized out-of-sync detection."""

    # Start command: needs sync when NOT in running/pending/starting
    @pytest.mark.parametrize("actual,expected", [
        ("undeployed", True), ("stopped", True), ("exited", True), ("error", True),
        ("running", False), ("pending", False), ("starting", False),
        ("stopping", True),
    ])
    def test_needs_sync_start(self, actual: str, expected: bool) -> None:
        assert NodeStateMachine.needs_sync(actual, "start") == expected

    # Stop command: needs sync when NOT in stopped/undeployed/stopping
    @pytest.mark.parametrize("actual,expected", [
        ("running", True), ("starting", True), ("pending", True),
        ("error", True), ("exited", True),
        ("stopped", False), ("undeployed", False), ("stopping", False),
    ])
    def test_needs_sync_stop(self, actual: str, expected: bool) -> None:
        assert NodeStateMachine.needs_sync(actual, "stop") == expected


# ---------------------------------------------------------------------------
# NodeStateMachine — compute_display_state (Phase 6.3)
# ---------------------------------------------------------------------------

class TestComputeDisplayState:
    """Test server-side display state computation."""

    def test_running(self) -> None:
        assert NodeStateMachine.compute_display_state("running", "running") == "running"

    def test_starting(self) -> None:
        assert NodeStateMachine.compute_display_state("starting", "running") == "starting"

    def test_stopping(self) -> None:
        assert NodeStateMachine.compute_display_state("stopping", "stopped") == "stopping"

    def test_stopped(self) -> None:
        assert NodeStateMachine.compute_display_state("stopped", "stopped") == "stopped"

    def test_exited(self) -> None:
        assert NodeStateMachine.compute_display_state("exited", "stopped") == "stopped"

    def test_undeployed(self) -> None:
        assert NodeStateMachine.compute_display_state("undeployed", "stopped") == "stopped"

    def test_error(self) -> None:
        assert NodeStateMachine.compute_display_state("error", "running") == "error"

    def test_error_desired_stopped(self) -> None:
        assert NodeStateMachine.compute_display_state("error", "stopped") == "error"

    def test_running_desired_stopped_shows_stopping(self) -> None:
        """When stop requested but container still running, show 'stopping'."""
        assert NodeStateMachine.compute_display_state("running", "stopped") == "stopping"

    def test_stopped_desired_running_shows_starting(self) -> None:
        """When start requested but container still stopped, show 'starting'."""
        assert NodeStateMachine.compute_display_state("stopped", "running") == "starting"

    def test_exited_desired_running_shows_starting(self) -> None:
        assert NodeStateMachine.compute_display_state("exited", "running") == "starting"

    def test_undeployed_desired_running_shows_starting(self) -> None:
        assert NodeStateMachine.compute_display_state("undeployed", "running") == "starting"

    def test_pending_desired_running(self) -> None:
        assert NodeStateMachine.compute_display_state("pending", "running") == "starting"

    def test_pending_desired_stopped(self) -> None:
        assert NodeStateMachine.compute_display_state("pending", "stopped") == "stopped"

    def test_unknown_state_defaults_to_error(self) -> None:
        assert NodeStateMachine.compute_display_state("bogus", "running") == "error"

    # Cross-product: verify all 8 actual × 2 desired combinations
    @pytest.mark.parametrize("actual,desired,expected", [
        ("undeployed", "stopped", "stopped"),
        ("undeployed", "running", "starting"),
        ("pending", "running", "starting"),
        ("pending", "stopped", "stopped"),
        ("starting", "running", "starting"),
        ("starting", "stopped", "starting"),
        ("running", "running", "running"),
        ("running", "stopped", "stopping"),
        ("stopping", "running", "stopping"),
        ("stopping", "stopped", "stopping"),
        ("stopped", "running", "starting"),
        ("stopped", "stopped", "stopped"),
        ("exited", "running", "starting"),
        ("exited", "stopped", "stopped"),
        ("error", "running", "error"),
        ("error", "stopped", "error"),
    ])
    def test_all_combinations(self, actual: str, desired: str, expected: str) -> None:
        assert NodeStateMachine.compute_display_state(actual, desired) == expected
