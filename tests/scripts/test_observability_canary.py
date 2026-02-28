from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


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
