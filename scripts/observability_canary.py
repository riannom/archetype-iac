#!/usr/bin/env python3
"""Run a controlled observability canary and coverage check.

Default mode is read-only. Use --apply to generate controlled traffic.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


TERMINAL_JOB_STATES = {"completed", "failed", "cancelled", "completed_with_warnings"}


@dataclass
class Config:
    api_url: str
    prometheus_url: str
    username: str
    password: str
    lab_id: str | None
    sync_node_id: str | None
    apply: bool
    run_up_down: bool
    status_probes: int
    scrape_wait_seconds: int
    job_timeout_seconds: int
    window: str


def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    form: dict[str, str] | None = None,
    payload: dict | None = None,
) -> dict:
    headers: dict[str, str] = {"Accept": "application/json"}
    data: bytes | None = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode()
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()

    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def _login(cfg: Config) -> str:
    res = _request_json(
        "POST",
        f"{cfg.api_url}/auth/login",
        form={"username": cfg.username, "password": cfg.password},
    )
    token = res.get("access_token")
    if not token:
        raise RuntimeError("Login failed: missing access_token")
    return token


def _prom_query(cfg: Config, expr: str) -> float:
    q = urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(
        urllib.request.Request(f"{cfg.prometheus_url}/api/v1/query?{q}"),
        timeout=30,
    ) as resp:
        data = json.loads(resp.read().decode())
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {expr}")
    results = data.get("data", {}).get("result", [])
    total = 0.0
    for row in results:
        value = row.get("value")
        if isinstance(value, list) and len(value) == 2:
            try:
                total += float(value[1])
            except Exception:
                pass
    return total


def _wait_for_job(cfg: Config, token: str, job_id: str) -> dict:
    if not cfg.lab_id:
        raise RuntimeError("lab_id is required for waiting on jobs")
    deadline = time.time() + cfg.job_timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        last = _request_json(
            "GET",
            f"{cfg.api_url}/labs/{cfg.lab_id}/jobs/{job_id}",
            token=token,
        )
        status = str(last.get("status", "")).lower()
        if status in TERMINAL_JOB_STATES:
            return last
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for job {job_id}; last_status={last.get('status')}")


def _run_canary_traffic(cfg: Config, token: str) -> None:
    if not cfg.lab_id:
        raise RuntimeError("--lab-id is required when --apply is set")

    print(f"[canary] status probes: {cfg.status_probes}")
    for _ in range(cfg.status_probes):
        _request_json("GET", f"{cfg.api_url}/labs/{cfg.lab_id}/status", token=token)
        time.sleep(1)

    if cfg.sync_node_id:
        print(f"[canary] sync node desired-state running: {cfg.sync_node_id}")
        _request_json(
            "PUT",
            f"{cfg.api_url}/labs/{cfg.lab_id}/nodes/{cfg.sync_node_id}/desired-state",
            token=token,
            payload={"state": "running"},
        )

    if cfg.run_up_down:
        print("[canary] running lab up/down")
        up = _request_json("POST", f"{cfg.api_url}/labs/{cfg.lab_id}/up", token=token)
        up_id = up.get("id")
        if not up_id:
            raise RuntimeError("Expected job id from /up")
        up_done = _wait_for_job(cfg, token, str(up_id))
        print(f"[canary] up job status={up_done.get('status')}")

        down = _request_json("POST", f"{cfg.api_url}/labs/{cfg.lab_id}/down", token=token)
        down_id = down.get("id")
        if not down_id:
            raise RuntimeError("Expected job id from /down")
        down_done = _wait_for_job(cfg, token, str(down_id))
        print(f"[canary] down job status={down_done.get('status')}")


def _print_coverage(cfg: Config) -> int:
    job_scope = 'job=~"archetype-(api|worker)"'
    checks = {
        "targets_up_api": 'up{job="archetype-api"}',
        "targets_up_agent": 'up{job="archetype-agent"}',
        "targets_up_worker": 'up{job="archetype-worker"}',
        "targets_up_scheduler": 'up{job="archetype-scheduler"}',
        "jobs_started": f'sum(increase(archetype_jobs_total{{{job_scope},status="started"}}[{cfg.window}]))',
        "jobs_started_worker": f'sum(increase(archetype_jobs_total{{job="archetype-worker",status="started"}}[{cfg.window}]))',
        "job_series_present": f'count(archetype_jobs_total{{{job_scope}}})',
        "job_duration_samples": f'sum(increase(archetype_job_duration_seconds_count{{{job_scope}}}[{cfg.window}]))',
        "job_queue_wait_samples": f'sum(increase(archetype_job_queue_wait_seconds_count{{{job_scope}}}[{cfg.window}]))',
        "job_failure_reason_samples": f'sum(increase(archetype_job_failures_total{{{job_scope}}}[{cfg.window}]))',
        "api_get_lab_status_samples": f'sum(increase(archetype_agent_operation_duration_seconds_count{{job="archetype-api",operation="get_lab_status"}}[{cfg.window}]))',
        "nlm_phase_samples": f'sum(increase(archetype_nlm_phase_duration_seconds_count{{job=~"archetype-(api|worker)"}}[{cfg.window}]))',
        "nlm_phase_samples_worker": f'sum(increase(archetype_nlm_phase_duration_seconds_count{{job="archetype-worker"}}[{cfg.window}]))',
        "agent_node_samples": f'sum(increase(archetype_agent_node_operation_seconds_count{{job="archetype-agent"}}[{cfg.window}]))',
        "agent_docker_samples": f'sum(increase(archetype_agent_docker_api_seconds_count{{job="archetype-agent"}}[{cfg.window}]))',
        "agent_ovs_samples": f'sum(increase(archetype_agent_ovs_operation_seconds_count{{job="archetype-agent"}}[{cfg.window}]))',
    }

    values: dict[str, float] = {}
    for name, expr in checks.items():
        values[name] = _prom_query(cfg, expr)

    print("[coverage]")
    print(json.dumps(values, indent=2, sort_keys=True))

    failures = 0
    for target in ("targets_up_api", "targets_up_agent", "targets_up_worker", "targets_up_scheduler"):
        if values[target] < 1:
            print(f"[fail] target down: {target}")
            failures += 1

    if values["jobs_started"] > 0:
        if values["jobs_started_worker"] <= 0:
            print("[warn] jobs started but none observed on worker; verify ownership model")
        if values["job_duration_samples"] <= 0:
            print("[fail] jobs started but no job_duration samples")
            failures += 1
        if values["job_queue_wait_samples"] <= 0:
            print("[fail] jobs started but no job_queue_wait samples")
            failures += 1
    elif values["job_series_present"] > 0:
        print("[info] job metric series present but no new starts in window")

    if failures == 0:
        print("[ok] coverage checks passed")
    return failures


def parse_args() -> Config:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--prometheus-url", default="http://localhost:9090")
    p.add_argument("--username", default="admin@example.com")
    p.add_argument("--password", default="changeme123")
    p.add_argument("--lab-id")
    p.add_argument("--sync-node-id")
    p.add_argument("--apply", action="store_true", help="Generate controlled traffic")
    p.add_argument("--run-up-down", action="store_true", help="Include lab up/down cycle (requires --apply)")
    p.add_argument("--status-probes", type=int, default=3)
    p.add_argument("--scrape-wait-seconds", type=int, default=20)
    p.add_argument("--job-timeout-seconds", type=int, default=1800)
    p.add_argument("--window", default="30m", help="Prometheus range window for increase() checks")
    a = p.parse_args()
    return Config(
        api_url=a.api_url.rstrip("/"),
        prometheus_url=a.prometheus_url.rstrip("/"),
        username=a.username,
        password=a.password,
        lab_id=a.lab_id,
        sync_node_id=a.sync_node_id,
        apply=a.apply,
        run_up_down=a.run_up_down,
        status_probes=max(1, a.status_probes),
        scrape_wait_seconds=max(0, a.scrape_wait_seconds),
        job_timeout_seconds=max(30, a.job_timeout_seconds),
        window=a.window,
    )


def main() -> int:
    cfg = parse_args()
    try:
        token = _login(cfg)
        if cfg.apply:
            _run_canary_traffic(cfg, token)
            if cfg.scrape_wait_seconds:
                print(f"[canary] waiting {cfg.scrape_wait_seconds}s for scrapes")
                time.sleep(cfg.scrape_wait_seconds)
        else:
            print("[canary] read-only mode (use --apply to generate traffic)")
        return 1 if _print_coverage(cfg) > 0 else 0
    except urllib.error.HTTPError as e:
        print(f"[error] HTTP {e.code}: {e.reason}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
