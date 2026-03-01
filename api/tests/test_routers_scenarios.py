from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.config import settings


def _scenario_path(lab_id: str, filename: str) -> Path:
    sdir = Path(settings.workspace) / lab_id / "scenarios"
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir / filename


def test_list_scenarios_includes_parse_errors(
    test_client: TestClient,
    sample_lab: models.Lab,
    auth_headers: dict[str, str],
):
    _scenario_path(
        sample_lab.id,
        "valid.yml",
    ).write_text(
        "name: Boot Check\ndescription: verify boot path\nsteps:\n  - type: wait\n    seconds: 1\n",
        encoding="utf-8",
    )
    _scenario_path(sample_lab.id, "broken.yaml").write_text(":\n  - not valid yaml", encoding="utf-8")
    _scenario_path(sample_lab.id, "ignore.txt").write_text("ignored", encoding="utf-8")

    resp = test_client.get(f"/labs/{sample_lab.id}/scenarios", headers=auth_headers)

    assert resp.status_code == 200
    body = {item["filename"]: item for item in resp.json()}
    assert set(body.keys()) == {"broken.yaml", "valid.yml"}
    assert body["valid.yml"]["name"] == "Boot Check"
    assert body["valid.yml"]["step_count"] == 1
    assert body["broken.yaml"]["description"] == "(parse error)"
    assert body["broken.yaml"]["step_count"] == 0


def test_get_and_save_scenario_round_trip(
    test_client: TestClient,
    sample_lab: models.Lab,
    auth_headers: dict[str, str],
):
    content = "name: L2 Check\nsteps:\n  - type: wait\n    seconds: 2\n"
    put_resp = test_client.put(
        f"/labs/{sample_lab.id}/scenarios/l2-check.yml",
        json={"content": content},
        headers=auth_headers,
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["ok"] is True

    get_resp = test_client.get(
        f"/labs/{sample_lab.id}/scenarios/l2-check.yml",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    payload = get_resp.json()
    assert payload["filename"] == "l2-check.yml"
    assert payload["name"] == "L2 Check"
    assert len(payload["steps"]) == 1
    assert "type: wait" in payload["raw_yaml"]


def test_save_scenario_rejects_invalid_filename(
    test_client: TestClient,
    sample_lab: models.Lab,
    auth_headers: dict[str, str],
):
    resp = test_client.put(
        f"/labs/{sample_lab.id}/scenarios/invalid@name.yml",
        json={"content": "name: Bad\nsteps:\n  - type: wait\n"},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert "Invalid filename" in resp.json()["detail"]


def test_execute_scenario_creates_job_and_dispatches_task(
    test_client: TestClient,
    test_db: Session,
    sample_lab: models.Lab,
    test_user: models.User,
    auth_headers: dict[str, str],
    monkeypatch,
):
    _scenario_path(
        sample_lab.id,
        "smoke.yml",
    ).write_text(
        "name: Smoke\nsteps:\n  - type: wait\n    seconds: 1\n  - type: wait\n    seconds: 1\n",
        encoding="utf-8",
    )

    from app.routers import scenarios as scenarios_router
    from app.tasks import scenario_executor

    scheduled_names: list[str] = []

    async def _fake_run_scenario(*_args, **_kwargs):
        return None

    def _capture_task(coro, name=None, **_kwargs):
        if name:
            scheduled_names.append(name)
        coro.close()
        return None

    monkeypatch.setattr(scenario_executor, "run_scenario", _fake_run_scenario)
    monkeypatch.setattr(scenarios_router, "safe_create_task", _capture_task)

    resp = test_client.post(
        f"/labs/{sample_lab.id}/scenarios/smoke.yml/execute",
        headers=auth_headers,
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["scenario_name"] == "Smoke"
    assert payload["step_count"] == 2
    assert payload["job_id"]

    job = test_db.get(models.Job, payload["job_id"])
    assert job is not None
    assert job.lab_id == sample_lab.id
    assert job.user_id == test_user.id
    assert job.action == "scenario"
    assert job.status == "queued"
    assert job.log_path and job.log_path.endswith("scenario_smoke.json")
    assert scheduled_names == [f"scenario:{payload['job_id']}"]
