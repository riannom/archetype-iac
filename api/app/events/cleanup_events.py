"""Cleanup event types and serialization for event-driven cleanup.

Events are published to a dedicated Redis channel (separate from lab_state
channels used by WebSocket clients) and consumed by CleanupEventHandler.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

CLEANUP_CHANNEL = "cleanup_events"


class CleanupEventType(str, Enum):
    LAB_DELETED = "lab_deleted"
    NODE_REMOVED = "node_removed"
    NODE_PLACEMENT_CHANGED = "node_placement_changed"
    LINK_REMOVED = "link_removed"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    AGENT_OFFLINE = "agent_offline"
    DEPLOY_FINISHED = "deploy_finished"
    DESTROY_FINISHED = "destroy_finished"
    STATE_CHECK_REQUESTED = "state_check_requested"


@dataclass
class CleanupEvent:
    event_type: CleanupEventType
    lab_id: str | None = None
    node_name: str | None = None
    agent_id: str | None = None
    old_agent_id: str | None = None
    job_id: str | None = None
    job_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> str:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return json.dumps(d)

    @classmethod
    def from_json(cls, data: str) -> CleanupEvent:
        d = json.loads(data)
        d["event_type"] = CleanupEventType(d["event_type"])
        return cls(**d)
