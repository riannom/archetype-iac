#!/usr/bin/env python3
"""Run a deterministic support-bundle triage drill.

This script is intended for CI/non-prod guardrails:
1) Seed a synthetic failed job with known failure markers.
2) Generate a support bundle through the public API.
3) Verify the bundle contains key observability artifacts and the seeded signal.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile


def _request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict | None = None,
    form: dict[str, str] | None = None,
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
        body = resp.read().decode() or "{}"
    return json.loads(body)


def _request_bytes(method: str, url: str, *, token: str | None = None) -> bytes:
    headers: dict[str, str] = {"Accept": "application/zip"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _login(api_url: str, username: str, password: str) -> str:
    payload = _request_json(
        "POST",
        f"{api_url}/auth/login",
        form={"username": username, "password": password},
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Login failed: missing access_token")
    return str(token)


def _seed_failure(compose_file: str) -> tuple[str, str]:
    seed_code = r"""
import json
from uuid import uuid4
from app import models
from app.db import get_session

message = (
    "ERROR: this session's transaction has been rolled back after an exception. "
    "Automatic recovery attempted but rollback failed while cleaning up."
)

with get_session() as session:
    user = (
        session.query(models.User)
        .filter(models.User.global_role == "super_admin")
        .order_by(models.User.created_at.asc())
        .first()
    )
    if user is None:
        raise RuntimeError("No super_admin user found for support-bundle drill")

    lab = models.Lab(
        name=f"ci-triage-lab-{uuid4().hex[:8]}",
        owner_id=user.id,
        provider="docker",
        state="error",
        state_error="Synthetic support-bundle triage drill",
    )
    session.add(lab)
    session.commit()
    session.refresh(lab)

    job = models.Job(
        lab_id=lab.id,
        user_id=user.id,
        action="up",
        status="failed",
        log_path=message,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    print(json.dumps({"lab_id": lab.id, "job_id": job.id}))
"""
    cmd = [
        "docker",
        "compose",
        "-f",
        compose_file,
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        seed_code,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "Failed to seed synthetic failure: "
            f"exit={completed.returncode} stderr={completed.stderr.strip()}"
        )

    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            lab_id = str(payload["lab_id"])
            job_id = str(payload["job_id"])
            if not lab_id or not job_id:
                continue
            return lab_id, job_id
        except Exception:
            continue

    raise RuntimeError(
        f"Could not parse seed output as JSON. stdout={completed.stdout!r}"
    )


def _create_bundle(api_url: str, token: str, *, lab_id: str, window_hours: int) -> str:
    request_body = {
        "summary": "CI support-bundle triage drill",
        "repro_steps": "Synthetic failed job seeded in database for deterministic signal validation.",
        "expected_behavior": "Generated bundle contains required observability artifacts and failure evidence.",
        "actual_behavior": "Bundle should include synthetic db-session-invalidated/rollback-failed markers.",
        "time_window_hours": window_hours,
        "impacted_lab_ids": [lab_id],
        "impacted_agent_ids": [],
        "include_configs": False,
        "pii_safe": True,
    }
    response = _request_json(
        "POST",
        f"{api_url}/support-bundles",
        token=token,
        payload=request_body,
    )
    bundle_id = response.get("id")
    if not bundle_id:
        raise RuntimeError("Support bundle creation failed: missing bundle id")
    return str(bundle_id)


def _wait_for_bundle(
    api_url: str,
    token: str,
    bundle_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict:
    deadline = time.time() + timeout_seconds
    last: dict = {}
    while time.time() < deadline:
        last = _request_json(
            "GET",
            f"{api_url}/support-bundles/{bundle_id}",
            token=token,
        )
        status = str(last.get("status") or "").lower()
        if status == "completed":
            return last
        if status == "failed":
            raise RuntimeError(
                "Support bundle generation failed: "
                f"{last.get('error_message') or 'unknown error'}"
            )
        time.sleep(max(1, poll_seconds))
    raise TimeoutError(
        f"Timed out waiting for support bundle {bundle_id}; last={last}"
    )


def _validate_bundle(bundle_zip: bytes, *, lab_id: str) -> None:
    required_files = {
        "manifest.json",
        "incident/user-report.json",
        "system/controller.json",
        "api/action-logs.json",
        "observability/prometheus.json",
        "observability/prometheus-targets.json",
        "observability/prometheus-alerts.json",
        "observability/loki-service-logs.json",
    }
    seeded_lab_path = f"labs/{lab_id}/bundle.json"

    with zipfile.ZipFile(io.BytesIO(bundle_zip), "r") as zf:
        names = set(zf.namelist())
        missing = sorted(path for path in required_files if path not in names)
        if missing:
            raise RuntimeError(
                "Bundle missing required artifacts: "
                + ", ".join(missing)
            )
        if seeded_lab_path not in names:
            raise RuntimeError(f"Bundle missing seeded lab artifact: {seeded_lab_path}")

        lab_payload = json.loads(zf.read(seeded_lab_path).decode())
        jobs = lab_payload.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            raise RuntimeError(
                f"Seeded lab bundle has no jobs for triage validation: {seeded_lab_path}"
            )

        combined_logs = "\n".join(str(job.get("log_excerpt") or "") for job in jobs).lower()
        required_markers = [
            "this session's transaction has been rolled back",
            "rollback failed",
        ]
        missing_markers = [marker for marker in required_markers if marker not in combined_logs]
        if missing_markers:
            raise RuntimeError(
                "Seeded failure markers not found in bundle jobs: "
                + ", ".join(missing_markers)
            )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--username", default="admin@example.com")
    p.add_argument("--password", default="changeme123")
    p.add_argument("--compose-file", default="docker-compose.gui.yml")
    p.add_argument("--time-window-hours", type=int, default=24)
    p.add_argument("--timeout-seconds", type=int, default=180)
    p.add_argument("--poll-seconds", type=int, default=2)
    p.add_argument("--output-zip", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    try:
        token = _login(api_url, args.username, args.password)
        lab_id, job_id = _seed_failure(args.compose_file)
        print(f"[triage-drill] seeded failed job: lab={lab_id} job={job_id}")

        bundle_id = _create_bundle(
            api_url,
            token,
            lab_id=lab_id,
            window_hours=max(1, min(int(args.time_window_hours), 168)),
        )
        print(f"[triage-drill] created support bundle: {bundle_id}")

        _wait_for_bundle(
            api_url,
            token,
            bundle_id,
            timeout_seconds=max(30, int(args.timeout_seconds)),
            poll_seconds=max(1, int(args.poll_seconds)),
        )
        print(f"[triage-drill] bundle completed: {bundle_id}")

        bundle_zip = _request_bytes(
            "GET",
            f"{api_url}/support-bundles/{bundle_id}/download",
            token=token,
        )
        if args.output_zip:
            with open(args.output_zip, "wb") as handle:
                handle.write(bundle_zip)
            print(f"[triage-drill] wrote bundle archive: {args.output_zip}")

        _validate_bundle(bundle_zip, lab_id=lab_id)
        print("[triage-drill] validation passed")
        return 0
    except Exception as exc:
        print(f"[triage-drill] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
