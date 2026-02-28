from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "support_bundle_triage_drill.py"
    spec = importlib.util.spec_from_file_location("support_bundle_triage_drill", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_bundle_bytes(
    lab_id: str,
    *,
    manifest_errors: list[str] | None = None,
    include_prometheus: bool = True,
) -> bytes:
    files: dict[str, dict | list] = {
        "manifest.json": {"errors": manifest_errors or []},
        "incident/user-report.json": {"summary": "test"},
        "system/controller.json": {"service": "archetype-api"},
        "system/control-plane-health.json": {
            "api": {"payload": {"status": "ok"}},
            "scheduler": {"payload": {"status": "ok"}},
            "worker": {"probe": {"ok": True}},
            "agent": {"sample_count": 1, "sampled_agents": []},
            "queue_snapshot": {"queue_depth": 0},
        },
        "system/queue-status.json": {"queue_depth": 0},
        "api/action-logs.json": {"jobs": [], "audit_logs": []},
        "observability/prometheus-targets.json": {"status": "success"},
        "observability/prometheus-alerts.json": {"status": "success"},
        "observability/loki-service-logs.json": {"api": {"data": {"result": []}}},
        f"labs/{lab_id}/bundle.json": {
            "jobs": [
                {
                    "id": "job-1",
                    "status": "failed",
                    "log_excerpt": (
                        "ERROR: this session's transaction has been rolled back after an exception. "
                        "Automatic recovery attempted but rollback failed while cleaning up."
                    ),
                }
            ]
        },
    }
    if include_prometheus:
        files["observability/prometheus.json"] = {"status": "success"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, payload in files.items():
            zf.writestr(path, json.dumps(payload))
    return buf.getvalue()


def test_validate_bundle_passes_with_required_artifacts():
    module = _load_module()
    lab_id = "lab-123"
    bundle = _build_bundle_bytes(lab_id)
    module._validate_bundle(
        bundle,
        lab_id=lab_id,
        allow_completeness_warnings=False,
    )


def test_validate_bundle_fails_when_required_artifact_missing():
    module = _load_module()
    lab_id = "lab-123"
    bundle = _build_bundle_bytes(lab_id, include_prometheus=False)
    with pytest.raises(RuntimeError, match="Bundle missing required artifacts"):
        module._validate_bundle(
            bundle,
            lab_id=lab_id,
            allow_completeness_warnings=False,
        )


def test_validate_bundle_fails_on_critical_completeness_warning():
    module = _load_module()
    lab_id = "lab-123"
    bundle = _build_bundle_bytes(
        lab_id,
        manifest_errors=["Coverage gap: failed to query Loki entries for service 'api'"],
    )
    with pytest.raises(RuntimeError, match="Bundle completeness warnings detected"):
        module._validate_bundle(
            bundle,
            lab_id=lab_id,
            allow_completeness_warnings=False,
        )


def test_validate_bundle_allows_completeness_warning_when_opted_in():
    module = _load_module()
    lab_id = "lab-123"
    bundle = _build_bundle_bytes(
        lab_id,
        manifest_errors=["Coverage gap: failed to query Loki entries for service 'api'"],
    )
    module._validate_bundle(
        bundle,
        lab_id=lab_id,
        allow_completeness_warnings=True,
    )
