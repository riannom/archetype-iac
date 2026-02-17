"""Tests for Grafana dashboard JSON files."""
import json
import re
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "grafana" / "dashboards"
DASHBOARD_FILES = list(DASHBOARD_DIR.glob("*.json"))

# Metrics defined in the codebase
KNOWN_METRICS = {
    # API metrics (api/app/metrics.py)
    "archetype_nlm_phase_duration_seconds",
    "archetype_agent_operation_duration_seconds",
    "archetype_nodes_total",
    "archetype_nodes_ready",
    "archetype_jobs_total",
    "archetype_job_duration_seconds",
    "archetype_job_queue_wait_seconds",
    "archetype_job_failures_total",
    "archetype_jobs_active",
    "archetype_agents_online",
    "archetype_agents_total",
    "archetype_enforcement_total",
    "archetype_enforcement_failures_total",
    "archetype_enforcement_pending",
    "archetype_labs_total",
    "archetype_labs_active",
    "archetype_link_endpoint_reservations_total",
    "archetype_link_endpoint_reservation_missing",
    "archetype_link_endpoint_reservation_orphaned",
    "archetype_link_endpoint_reservation_conflicts",
    # Agent metrics (agent/metrics.py)
    "archetype_agent_docker_api_seconds",
    "archetype_agent_ovs_operation_seconds",
    "archetype_agent_node_operation_seconds",
    "archetype_agent_node_operation_errors_total",
}


@pytest.fixture(params=DASHBOARD_FILES, ids=lambda p: p.stem)
def dashboard(request):
    """Load each dashboard JSON file."""
    with open(request.param) as f:
        return json.load(f)


def test_dashboard_json_valid():
    """All dashboard files should be valid JSON."""
    for path in DASHBOARD_DIR.glob("*.json"):
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict), f"{path.name} is not a JSON object"


def test_dashboard_has_required_fields(dashboard):
    """Each dashboard must have title, panels, uid."""
    assert "title" in dashboard, "Missing 'title'"
    assert "panels" in dashboard, "Missing 'panels'"
    assert "uid" in dashboard, "Missing 'uid'"
    assert len(dashboard["panels"]) > 0, "Dashboard has no panels"


def test_no_duplicate_panel_ids(dashboard):
    """Panel IDs must be unique within a dashboard."""
    ids = [p["id"] for p in dashboard["panels"] if "id" in p]
    assert len(ids) == len(set(ids)), f"Duplicate panel IDs in {dashboard['title']}: {ids}"


def test_datasource_references_valid(dashboard):
    """Panel datasources should reference prometheus or loki."""
    valid_uids = {"prometheus", "loki"}
    for panel in dashboard["panels"]:
        ds = panel.get("datasource", {})
        if ds:
            uid = ds.get("uid", "")
            assert uid in valid_uids, (
                f"Panel '{panel.get('title')}' in {dashboard['title']} "
                f"has invalid datasource uid: {uid}"
            )


def test_metric_names_exist_in_codebase(dashboard):
    """Metric names referenced in queries should exist in the codebase."""
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            # Extract metric names (archetype_*)
            metrics = re.findall(r"(archetype_\w+?)(?:_bucket|_count|_sum|\{|\)| )", expr)
            for metric in metrics:
                # Strip _bucket/_count/_sum suffixes that Prometheus adds
                base = re.sub(r"_(bucket|count|sum|total)$", "", metric)
                if base + "_total" in KNOWN_METRICS:
                    continue
                assert metric in KNOWN_METRICS or base in KNOWN_METRICS, (
                    f"Metric '{metric}' in panel '{panel.get('title')}' "
                    f"({dashboard['title']}) not found in codebase"
                )
