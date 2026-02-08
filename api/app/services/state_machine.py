"""State machine services for centralized state transition logic.

This module provides state machines for nodes and links, validating
transitions and computing next states based on desired states.
"""

from typing import Optional

from app.state import (
    LabState,
    LinkActualState,
    LinkDesiredState,
    NodeActualState,
    NodeDesiredState,
)


class NodeStateMachine:
    """Centralized state transition logic for nodes.

    Node state lifecycle:
        undeployed -> pending -> running (first deploy)
        stopped -> starting -> running (subsequent starts)
        running -> stopping -> stopped
        any -> error (on failure)
        error -> pending (retry)
    """

    VALID_TRANSITIONS: dict[NodeActualState, set[NodeActualState]] = {
        NodeActualState.UNDEPLOYED: {NodeActualState.PENDING, NodeActualState.ERROR},
        NodeActualState.PENDING: {NodeActualState.STARTING, NodeActualState.RUNNING, NodeActualState.UNDEPLOYED, NodeActualState.ERROR},
        NodeActualState.STARTING: {NodeActualState.RUNNING, NodeActualState.STOPPED, NodeActualState.ERROR},
        NodeActualState.RUNNING: {NodeActualState.STOPPING, NodeActualState.STOPPED, NodeActualState.ERROR},
        NodeActualState.STOPPING: {NodeActualState.STOPPED, NodeActualState.ERROR},
        NodeActualState.STOPPED: {NodeActualState.STARTING, NodeActualState.PENDING, NodeActualState.UNDEPLOYED, NodeActualState.ERROR},
        NodeActualState.EXITED: {NodeActualState.STARTING, NodeActualState.PENDING, NodeActualState.STOPPED, NodeActualState.ERROR},
        NodeActualState.ERROR: {NodeActualState.PENDING, NodeActualState.STARTING, NodeActualState.STOPPED, NodeActualState.UNDEPLOYED},
    }

    # States that indicate the container is in a stable state (no pending operations)
    TERMINAL_STATES: set[NodeActualState] = {
        NodeActualState.RUNNING,
        NodeActualState.STOPPED,
        NodeActualState.ERROR,
        NodeActualState.UNDEPLOYED,
    }

    # States that indicate the container exists (even if stopped)
    CONTAINER_EXISTS_STATES: set[NodeActualState] = {
        NodeActualState.RUNNING,
        NodeActualState.STOPPED,
        NodeActualState.STOPPING,
        NodeActualState.STARTING,
        NodeActualState.EXITED,
        NodeActualState.ERROR,
    }

    # States that should be treated as "stopped" for enforcement purposes
    # (i.e., states from which a "start" action should be triggered when desired=running)
    STOPPED_EQUIVALENT_STATES: set[NodeActualState] = {
        NodeActualState.STOPPED,
        NodeActualState.EXITED,
        NodeActualState.UNDEPLOYED,
        NodeActualState.PENDING,  # Node awaiting deployment
    }

    @classmethod
    def can_transition(cls, current: NodeActualState, target: NodeActualState) -> bool:
        """Check if a state transition is valid."""
        if current == target:
            return True
        return target in cls.VALID_TRANSITIONS.get(current, set())

    @classmethod
    def get_transition_for_desired(
        cls,
        current: NodeActualState,
        desired: NodeDesiredState,
    ) -> Optional[NodeActualState]:
        """Get the next state to move toward the desired state.

        Returns None if no transition is needed or possible.
        """
        if desired == NodeDesiredState.RUNNING:
            if current in cls.STOPPED_EQUIVALENT_STATES:
                # Need to start - use pending for undeployed, starting for stopped/pending
                if current == NodeActualState.UNDEPLOYED:
                    return NodeActualState.PENDING
                if current == NodeActualState.PENDING:
                    return NodeActualState.STARTING  # Retry deploy
                return NodeActualState.STARTING
            if current == NodeActualState.ERROR:
                return NodeActualState.PENDING
        elif desired == NodeDesiredState.STOPPED:
            if current == NodeActualState.RUNNING:
                return NodeActualState.STOPPING
            if current == NodeActualState.PENDING:
                # Abort pending deployment - no container exists yet
                return NodeActualState.UNDEPLOYED
        return None

    @classmethod
    def is_terminal(cls, state: NodeActualState) -> bool:
        """Check if state is terminal (no automatic transitions pending)."""
        return state in cls.TERMINAL_STATES

    @classmethod
    def matches_desired(cls, actual: NodeActualState, desired: NodeDesiredState) -> bool:
        """Check if actual state matches the desired state."""
        if desired == NodeDesiredState.RUNNING:
            return actual == NodeActualState.RUNNING
        elif desired == NodeDesiredState.STOPPED:
            return actual in cls.STOPPED_EQUIVALENT_STATES
        return False

    @classmethod
    def needs_enforcement(cls, actual: NodeActualState, desired: NodeDesiredState) -> bool:
        """Check if state enforcement is needed to reach desired state."""
        if cls.matches_desired(actual, desired):
            return False
        # Don't enforce during transitional states
        if actual in (NodeActualState.PENDING, NodeActualState.STARTING, NodeActualState.STOPPING):
            return False
        return True

    @classmethod
    def get_enforcement_action(cls, actual: NodeActualState, desired: NodeDesiredState) -> Optional[str]:
        """Get the enforcement action needed to reach desired state.

        Returns 'start', 'stop', or None if no action needed.
        """
        if not cls.needs_enforcement(actual, desired):
            return None
        if desired == NodeDesiredState.RUNNING:
            if actual in cls.STOPPED_EQUIVALENT_STATES or actual == NodeActualState.ERROR:
                return "start"
        elif desired == NodeDesiredState.STOPPED:
            if actual == NodeActualState.RUNNING:
                return "stop"
        return None

    # ------------------------------------------------------------------
    # Command guard methods (Phase 6.1)
    # ------------------------------------------------------------------

    @classmethod
    def can_accept_command(cls, actual_state: str, command: str) -> tuple[bool, str]:
        """Check if a node can accept a start/stop command.

        Returns (True, "") if allowed, (False, reason) if blocked.
        Blocks: start while stopping, stop while starting.
        """
        if command == "start" and actual_state == "stopping":
            return False, "Cannot start: node is currently stopping"
        if command == "stop" and actual_state == "starting":
            return False, "Cannot stop: node is currently starting"
        return True, ""

    @classmethod
    def can_accept_bulk_command(cls, actual_state: str, desired_state: str, command: str) -> tuple[str, str]:
        """Classify a node for bulk command processing.

        Returns:
            ("skip_transitional", reason) — node in transitional state
            ("already_in_state", reason) — already at desired state
            ("reset_and_proceed", "") — error node needing retry
            ("proceed", "") — actionable
        """
        # Skip transitional
        if actual_state in ("starting", "stopping", "pending"):
            return "skip_transitional", f"Node in transitional state: {actual_state}"

        # Already in desired state
        if command == "start":
            if actual_state == "running" and desired_state == "running":
                return "already_in_state", "Already running"
        else:  # stop
            if actual_state in ("stopped", "undeployed") and desired_state == "stopped":
                return "already_in_state", "Already stopped"

        # Error node being retried
        if actual_state == "error" and command == "start" and desired_state == "running":
            return "reset_and_proceed", ""

        return "proceed", ""

    @classmethod
    def needs_sync(cls, actual_state: str, command: str) -> bool:
        """Check if actual_state is out of sync with the command's target.

        True means a sync job should be created.
        """
        if command == "start":
            return actual_state not in ("running", "pending", "starting")
        else:  # stop
            return actual_state not in ("stopped", "undeployed", "stopping")

    @classmethod
    def compute_display_state(cls, actual_state: str, desired_state: str) -> str:
        """Map 8 internal states to 5 display states.

        Display states: running, starting, stopping, stopped, error.
        """
        if actual_state == "pending":
            return "starting" if desired_state == "running" else "stopped"

        # When desired diverges from actual, show transitional state
        # This prevents flashing "running" while a stop is in progress
        if actual_state == "running" and desired_state == "stopped":
            return "stopping"
        if actual_state in ("stopped", "exited", "undeployed") and desired_state == "running":
            return "starting"

        display_map = {
            "running": "running",
            "starting": "starting",
            "stopping": "stopping",
            "stopped": "stopped",
            "exited": "stopped",
            "undeployed": "stopped",
            "error": "error",
        }
        return display_map.get(actual_state, "error")


class LinkStateMachine:
    """Centralized state transition logic for links.

    Link state lifecycle:
        unknown -> pending -> creating -> up
        creating -> error (on failure)
        up <-> down (via desired_state changes)
        error -> pending (retry)
    """

    VALID_TRANSITIONS: dict[LinkActualState, set[LinkActualState]] = {
        LinkActualState.UNKNOWN: {LinkActualState.PENDING, LinkActualState.UP, LinkActualState.DOWN},
        LinkActualState.PENDING: {LinkActualState.CREATING, LinkActualState.UP, LinkActualState.ERROR},
        LinkActualState.CREATING: {LinkActualState.UP, LinkActualState.DOWN, LinkActualState.ERROR},
        LinkActualState.UP: {LinkActualState.DOWN, LinkActualState.ERROR},
        LinkActualState.DOWN: {LinkActualState.PENDING, LinkActualState.UP, LinkActualState.ERROR},
        LinkActualState.ERROR: {LinkActualState.PENDING, LinkActualState.DOWN, LinkActualState.UP},
    }

    # States eligible for auto-connect attempts
    CONNECTABLE_STATES: set[LinkActualState] = {
        LinkActualState.UNKNOWN,
        LinkActualState.PENDING,
        LinkActualState.DOWN,
        LinkActualState.ERROR,
    }

    @classmethod
    def can_transition(cls, current: LinkActualState, target: LinkActualState) -> bool:
        """Check if a state transition is valid."""
        if current == target:
            return True
        return target in cls.VALID_TRANSITIONS.get(current, set())

    @classmethod
    def matches_desired(cls, actual: LinkActualState, desired: LinkDesiredState) -> bool:
        """Check if actual state matches the desired state."""
        if desired == LinkDesiredState.UP:
            return actual == LinkActualState.UP
        elif desired == LinkDesiredState.DOWN:
            return actual == LinkActualState.DOWN
        return False

    @classmethod
    def should_auto_connect(
        cls,
        actual: LinkActualState,
        desired: LinkDesiredState,
        source_node_running: bool,
        target_node_running: bool,
    ) -> bool:
        """Determine if a link should be auto-connected.

        Links are auto-connected when:
        - Desired state is UP
        - Both endpoint nodes are running
        - Current state is eligible for connection
        """
        return (
            desired == LinkDesiredState.UP
            and source_node_running
            and target_node_running
            and actual in cls.CONNECTABLE_STATES
        )


class LabStateMachine:
    """Centralized state aggregation logic for labs.

    Lab state is derived from the aggregate state of all its nodes.
    """

    @classmethod
    def compute_lab_state(
        cls,
        running_count: int,
        stopped_count: int,
        undeployed_count: int,
        error_count: int,
        pending_count: int = 0,
        starting_count: int = 0,
        stopping_count: int = 0,
    ) -> LabState:
        """Compute lab state from node state counts.

        Priority:
        1. Any errors -> error
        2. Any transitional states -> appropriate transitional state
        3. All running -> running
        4. All stopped/undeployed -> stopped
        5. Mixed running/stopped -> running (partial is valid)
        """
        total = running_count + stopped_count + undeployed_count + error_count + pending_count + starting_count + stopping_count

        if total == 0:
            return LabState.STOPPED

        if error_count > 0:
            return LabState.ERROR

        # Check transitional states
        if stopping_count > 0:
            return LabState.STOPPING
        if starting_count > 0 or pending_count > 0:
            return LabState.STARTING

        # Terminal states
        if running_count > 0 and stopped_count == 0 and undeployed_count == 0:
            return LabState.RUNNING
        if running_count == 0 and (stopped_count > 0 or undeployed_count > 0):
            return LabState.STOPPED
        if running_count > 0:
            # Mixed state - some running, some stopped
            return LabState.RUNNING

        return LabState.UNKNOWN

    @classmethod
    def is_transitional(cls, state: LabState) -> bool:
        """Check if lab is in a transitional state."""
        return state in (LabState.STARTING, LabState.STOPPING, LabState.UNKNOWN)
