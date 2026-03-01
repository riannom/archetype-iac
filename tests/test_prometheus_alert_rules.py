"""Guardrail tests for required Prometheus alert rules."""

from __future__ import annotations

import re
from pathlib import Path


ALERT_RULES_PATH = Path(__file__).resolve().parents[1] / "prometheus-alerts.yml"


def _load_alert_blocks() -> dict[str, str]:
    text = ALERT_RULES_PATH.read_text(encoding="utf-8")
    blocks: dict[str, list[str]] = {}
    current_alert: str | None = None
    for line in text.splitlines():
        match = re.match(r"\s*-\s*alert:\s*([A-Za-z0-9_]+)\s*$", line)
        if match:
            current_alert = match.group(1)
            blocks[current_alert] = [line]
            continue
        if current_alert is not None:
            blocks[current_alert].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def test_required_alert_names_present() -> None:
    blocks = _load_alert_blocks()
    required = {
        "JobMetricsCoverageGap",
        "NlmMetricsCoverageGap",
        "DbSessionInvalidatedFailures",
        "DeployRollbackFailures",
        "LinkEndpointReservationDrift",
        "LinkEndpointReservationConflict",
    }
    missing = sorted(required - set(blocks))
    assert not missing, f"Missing required alert rules: {', '.join(missing)}"


def test_required_alert_expressions_present() -> None:
    blocks = _load_alert_blocks()
    expected_substrings = {
        "JobMetricsCoverageGap": [
            "archetype:coverage:jobs_started:2h > 0",
            "archetype:coverage:job_duration_samples:2h == 0",
            "archetype:coverage:job_queue_samples:2h == 0",
        ],
        "NlmMetricsCoverageGap": [
            'action="sync"',
            "archetype:coverage:nlm_samples:2h == 0",
        ],
        "DbSessionInvalidatedFailures": [
            'reason="db_session_invalidated"',
        ],
        "DeployRollbackFailures": [
            'reason="deploy_rollback_failed"',
        ],
        "LinkEndpointReservationDrift": [
            "archetype_link_endpoint_reservation_missing",
            "archetype_link_endpoint_reservation_orphaned",
        ],
        "LinkEndpointReservationConflict": [
            "archetype_link_endpoint_reservation_conflicts",
        ],
    }
    for alert_name, required_parts in expected_substrings.items():
        block = blocks.get(alert_name, "")
        for part in required_parts:
            assert part in block, f"Alert {alert_name} missing required expression fragment: {part}"
