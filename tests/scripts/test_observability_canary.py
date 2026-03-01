from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import urllib.error


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "observability_canary.py"
    spec = importlib.util.spec_from_file_location("observability_canary", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_auto_discovery_sets_apply_targets():
    module = _load_module()
    cfg = module.Config(
        api_url="http://api.local",
        prometheus_url="http://prom.local",
        username="user",
        password="pass",
        lab_id=None,
        sync_node_id=None,
        apply=True,
        run_up_down=False,
        status_probes=1,
        scrape_wait_seconds=0,
        job_timeout_seconds=30,
        window="30m",
        auto_discover=True,
    )

    def fake_request_json(method, url, *, token=None, form=None, payload=None):
        if method == "GET" and url == "http://api.local/labs?skip=0&limit=200":
            return {
                "labs": [
                    {"id": "lab-stopped", "state": "stopped", "running_count": 0},
                    {"id": "lab-running", "state": "running", "running_count": 2},
                ]
            }
        if method == "GET" and url == "http://api.local/labs/lab-running/nodes/states":
            return {
                "nodes": [
                    {
                        "node_id": "node-sync",
                        "desired_state": "running",
                        "actual_state": "running",
                    }
                ]
            }
        raise AssertionError(f"Unexpected API call: {method} {url}")

    module._request_json = fake_request_json
    module._ensure_apply_targets(cfg, token="token")

    assert cfg.lab_id == "lab-running"
    assert cfg.sync_node_id == "node-sync"


def test_apply_mode_requires_status_probe_samples():
    module = _load_module()
    cfg = module.Config(
        api_url="http://api.local",
        prometheus_url="http://prom.local",
        username="user",
        password="pass",
        lab_id="lab-1",
        sync_node_id="node-1",
        apply=True,
        run_up_down=False,
        status_probes=3,
        scrape_wait_seconds=0,
        job_timeout_seconds=30,
        window="30m",
        auto_discover=False,
    )

    checks = {
        "targets_up_api": 1.0,
        "targets_up_agent": 1.0,
        "targets_up_worker": 1.0,
        "targets_up_scheduler": 1.0,
        "jobs_started": 0.0,
        "jobs_started_worker": 0.0,
        "job_series_present": 0.0,
        "job_duration_samples": 0.0,
        "job_queue_wait_samples": 0.0,
        "job_failure_reason_samples": 0.0,
        "api_get_lab_status_samples": 0.0,
        "nlm_phase_samples": 0.0,
        "nlm_phase_samples_worker": 0.0,
        "agent_node_samples": 0.0,
        "agent_docker_samples": 0.0,
        "agent_ovs_samples": 0.0,
    }

    def fake_prom_query(_cfg, expr):
        if 'up{job="archetype-api"}' in expr:
            return checks["targets_up_api"]
        if 'up{job="archetype-agent"}' in expr:
            return checks["targets_up_agent"]
        if 'up{job="archetype-worker"}' in expr:
            return checks["targets_up_worker"]
        if 'up{job="archetype-scheduler"}' in expr:
            return checks["targets_up_scheduler"]
        if 'sum(increase(archetype_jobs_total{job=~"archetype-(api|worker)",status="started"}' in expr:
            return checks["jobs_started"]
        if 'sum(increase(archetype_jobs_total{job="archetype-worker",status="started"}' in expr:
            return checks["jobs_started_worker"]
        if "count(archetype_jobs_total" in expr:
            return checks["job_series_present"]
        if "archetype_job_duration_seconds_count" in expr:
            return checks["job_duration_samples"]
        if "archetype_job_queue_wait_seconds_count" in expr:
            return checks["job_queue_wait_samples"]
        if "archetype_job_failures_total" in expr:
            return checks["job_failure_reason_samples"]
        if 'archetype_agent_operation_duration_seconds_count{job="archetype-api",operation="get_lab_status"}' in expr:
            return checks["api_get_lab_status_samples"]
        if "archetype_nlm_phase_duration_seconds_count" in expr and 'job="archetype-worker"' in expr:
            return checks["nlm_phase_samples_worker"]
        if "archetype_nlm_phase_duration_seconds_count" in expr:
            return checks["nlm_phase_samples"]
        if "archetype_agent_node_operation_seconds_count" in expr:
            return checks["agent_node_samples"]
        if "archetype_agent_docker_api_seconds_count" in expr:
            return checks["agent_docker_samples"]
        if "archetype_agent_ovs_operation_seconds_count" in expr:
            return checks["agent_ovs_samples"]
        return 0.0

    module._prom_query = fake_prom_query

    failures = module._print_coverage(cfg)
    assert failures >= 1


def test_apply_mode_passes_with_status_probe_samples():
    module = _load_module()
    cfg = module.Config(
        api_url="http://api.local",
        prometheus_url="http://prom.local",
        username="user",
        password="pass",
        lab_id="lab-1",
        sync_node_id=None,
        apply=True,
        run_up_down=False,
        status_probes=3,
        scrape_wait_seconds=0,
        job_timeout_seconds=30,
        window="30m",
        auto_discover=False,
    )

    checks = {
        "targets_up_api": 1.0,
        "targets_up_agent": 1.0,
        "targets_up_worker": 1.0,
        "targets_up_scheduler": 1.0,
        "jobs_started": 0.0,
        "jobs_started_worker": 0.0,
        "job_series_present": 0.0,
        "job_duration_samples": 0.0,
        "job_queue_wait_samples": 0.0,
        "job_failure_reason_samples": 0.0,
        "api_get_lab_status_samples": 2.0,
        "nlm_phase_samples": 0.0,
        "nlm_phase_samples_worker": 0.0,
        "agent_node_samples": 0.0,
        "agent_docker_samples": 0.0,
        "agent_ovs_samples": 0.0,
    }

    def fake_prom_query(_cfg, expr):
        if 'up{job="archetype-api"}' in expr:
            return checks["targets_up_api"]
        if 'up{job="archetype-agent"}' in expr:
            return checks["targets_up_agent"]
        if 'up{job="archetype-worker"}' in expr:
            return checks["targets_up_worker"]
        if 'up{job="archetype-scheduler"}' in expr:
            return checks["targets_up_scheduler"]
        if 'sum(increase(archetype_jobs_total{job=~"archetype-(api|worker)",status="started"}' in expr:
            return checks["jobs_started"]
        if 'sum(increase(archetype_jobs_total{job="archetype-worker",status="started"}' in expr:
            return checks["jobs_started_worker"]
        if "count(archetype_jobs_total" in expr:
            return checks["job_series_present"]
        if "archetype_job_duration_seconds_count" in expr:
            return checks["job_duration_samples"]
        if "archetype_job_queue_wait_seconds_count" in expr:
            return checks["job_queue_wait_samples"]
        if "archetype_job_failures_total" in expr:
            return checks["job_failure_reason_samples"]
        if 'archetype_agent_operation_duration_seconds_count{job="archetype-api",operation="get_lab_status"}' in expr:
            return checks["api_get_lab_status_samples"]
        if "archetype_nlm_phase_duration_seconds_count" in expr and 'job="archetype-worker"' in expr:
            return checks["nlm_phase_samples_worker"]
        if "archetype_nlm_phase_duration_seconds_count" in expr:
            return checks["nlm_phase_samples"]
        if "archetype_agent_node_operation_seconds_count" in expr:
            return checks["agent_node_samples"]
        if "archetype_agent_docker_api_seconds_count" in expr:
            return checks["agent_docker_samples"]
        if "archetype_agent_ovs_operation_seconds_count" in expr:
            return checks["agent_ovs_samples"]
        return 0.0

    module._prom_query = fake_prom_query

    failures = module._print_coverage(cfg)
    assert failures == 0


def test_apply_mode_without_sync_node_allows_missing_status_probe_samples():
    module = _load_module()
    cfg = module.Config(
        api_url="http://api.local",
        prometheus_url="http://prom.local",
        username="user",
        password="pass",
        lab_id="lab-1",
        sync_node_id=None,
        apply=True,
        run_up_down=False,
        status_probes=3,
        scrape_wait_seconds=0,
        job_timeout_seconds=30,
        window="30m",
        auto_discover=False,
    )

    checks = {
        "targets_up_api": 1.0,
        "targets_up_agent": 1.0,
        "targets_up_worker": 1.0,
        "targets_up_scheduler": 1.0,
        "jobs_started": 0.0,
        "jobs_started_worker": 0.0,
        "job_series_present": 0.0,
        "job_duration_samples": 0.0,
        "job_queue_wait_samples": 0.0,
        "job_failure_reason_samples": 0.0,
        "api_get_lab_status_samples": 0.0,
        "nlm_phase_samples": 0.0,
        "nlm_phase_samples_worker": 0.0,
        "agent_node_samples": 0.0,
        "agent_docker_samples": 0.0,
        "agent_ovs_samples": 0.0,
    }

    def fake_prom_query(_cfg, expr):
        if 'up{job="archetype-api"}' in expr:
            return checks["targets_up_api"]
        if 'up{job="archetype-agent"}' in expr:
            return checks["targets_up_agent"]
        if 'up{job="archetype-worker"}' in expr:
            return checks["targets_up_worker"]
        if 'up{job="archetype-scheduler"}' in expr:
            return checks["targets_up_scheduler"]
        if 'sum(increase(archetype_jobs_total{job=~"archetype-(api|worker)",status="started"}' in expr:
            return checks["jobs_started"]
        if 'sum(increase(archetype_jobs_total{job="archetype-worker",status="started"}' in expr:
            return checks["jobs_started_worker"]
        if "count(archetype_jobs_total" in expr:
            return checks["job_series_present"]
        if "archetype_job_duration_seconds_count" in expr:
            return checks["job_duration_samples"]
        if "archetype_job_queue_wait_seconds_count" in expr:
            return checks["job_queue_wait_samples"]
        if "archetype_job_failures_total" in expr:
            return checks["job_failure_reason_samples"]
        if 'archetype_agent_operation_duration_seconds_count{job="archetype-api",operation="get_lab_status"}' in expr:
            return checks["api_get_lab_status_samples"]
        if "archetype_nlm_phase_duration_seconds_count" in expr and 'job="archetype-worker"' in expr:
            return checks["nlm_phase_samples_worker"]
        if "archetype_nlm_phase_duration_seconds_count" in expr:
            return checks["nlm_phase_samples"]
        if "archetype_agent_node_operation_seconds_count" in expr:
            return checks["agent_node_samples"]
        if "archetype_agent_docker_api_seconds_count" in expr:
            return checks["agent_docker_samples"]
        if "archetype_agent_ovs_operation_seconds_count" in expr:
            return checks["agent_ovs_samples"]
        return 0.0

    module._prom_query = fake_prom_query

    failures = module._print_coverage(cfg)
    assert failures == 0


def test_run_canary_traffic_ignores_sync_http_errors():
    module = _load_module()
    cfg = module.Config(
        api_url="http://api.local",
        prometheus_url="http://prom.local",
        username="user",
        password="pass",
        lab_id="lab-1",
        sync_node_id="node-1",
        apply=True,
        run_up_down=False,
        status_probes=3,
        scrape_wait_seconds=0,
        job_timeout_seconds=30,
        window="30m",
        auto_discover=False,
    )

    calls: list[tuple[str, str]] = []

    def fake_request_json(method, url, *, token=None, form=None, payload=None):
        calls.append((method, url))
        if method == "GET" and url == "http://api.local/labs/lab-1/status":
            return {}
        if method == "PUT" and url == "http://api.local/labs/lab-1/nodes/node-1/desired-state":
            raise urllib.error.HTTPError(url=url, code=500, msg="Internal Server Error", hdrs=None, fp=None)
        raise AssertionError(f"Unexpected API call: {method} {url}")

    module._request_json = fake_request_json
    module.time.sleep = lambda _seconds: None

    module._run_canary_traffic(cfg, token="token")

    assert calls.count(("GET", "http://api.local/labs/lab-1/status")) == 3
    assert ("PUT", "http://api.local/labs/lab-1/nodes/node-1/desired-state") in calls
