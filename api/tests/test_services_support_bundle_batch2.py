from __future__ import annotations

import json
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import models
from app.services import support_bundle as support_bundle_module


class _DummyResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | list | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {"content-type": "application/json"}
        self.is_success = 200 <= status_code < 300

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        result = self._handler(url, params)
        if isinstance(result, Exception):
            raise result
        return result


def test_int_env_and_bundle_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SB_BATCH2_INT", "not-an-int")
    assert support_bundle_module._int_env("SB_BATCH2_INT", 10, minimum=1, maximum=20) == 10

    monkeypatch.setenv("SB_BATCH2_INT", "-5")
    assert support_bundle_module._int_env("SB_BATCH2_INT", 10, minimum=1, maximum=20) == 1

    monkeypatch.setenv("SB_BATCH2_INT", "100")
    assert support_bundle_module._int_env("SB_BATCH2_INT", 10, minimum=1, maximum=20) == 20

    monkeypatch.setattr(support_bundle_module.settings, "workspace", str(tmp_path))
    bundle_dir = support_bundle_module._bundle_dir()
    assert bundle_dir == tmp_path / "support-bundles"
    assert bundle_dir.exists()


def test_parse_iso_datetime_and_collection_windows(monkeypatch) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(support_bundle_module, "_now_utc", lambda: now)

    assert support_bundle_module._parse_iso_datetime(None) is None
    assert support_bundle_module._parse_iso_datetime("not-a-date") is None

    naive = support_bundle_module._parse_iso_datetime("2026-02-28T10:00:00")
    assert naive is not None and naive.tzinfo is not None

    since, until = support_bundle_module._resolve_collection_window({}, 4)
    assert since == now - timedelta(hours=4)
    assert until == now

    start_only = {"incident_started_at": "2026-03-01T08:00:00Z"}
    since, until = support_bundle_module._resolve_collection_window(start_only, 2)
    assert since.isoformat() == "2026-03-01T08:00:00+00:00"
    assert until.isoformat() == "2026-03-01T10:00:00+00:00"

    end_only = {"incident_ended_at": "2026-03-01T09:00:00Z"}
    since, until = support_bundle_module._resolve_collection_window(end_only, 3)
    assert since.isoformat() == "2026-03-01T06:00:00+00:00"
    assert until.isoformat() == "2026-03-01T09:00:00+00:00"

    # Since > until after clamping should re-normalize to default window.
    invalid_window = {
        "incident_started_at": "2026-03-02T09:00:00Z",
        "incident_ended_at": "2026-03-01T01:00:00Z",
    }
    since, until = support_bundle_module._resolve_collection_window(invalid_window, 5)
    assert until.isoformat() == "2026-03-01T01:00:00+00:00"
    assert since.isoformat() == "2026-02-28T20:00:00+00:00"


def test_metric_parsers_and_log_excerpt_helpers() -> None:
    assert support_bundle_module._prometheus_scalar_sum(None) is None
    assert support_bundle_module._prometheus_scalar_sum({"data": {"result": "bad"}}) is None
    assert support_bundle_module._prometheus_scalar_sum({"data": {"result": []}}) == 0.0
    assert support_bundle_module._prometheus_scalar_sum(
        {
            "data": {
                "result": [
                    {"value": [1, "3"]},
                    {"value": [1, "2.5"]},
                    {"value": [1, "bad"]},
                ]
            }
        }
    ) == 5.5

    assert support_bundle_module._loki_entry_count(None) is None
    assert support_bundle_module._loki_entry_count({"error": "boom"}) is None
    assert support_bundle_module._loki_entry_count({"data": {"result": "bad"}}) == 0
    assert support_bundle_module._loki_entry_count(
        {
            "data": {
                "result": [
                    {"values": [["1", "a"], ["2", "b"]]},
                    {"values": [["3", "c"]]},
                ]
            }
        }
    ) == 3

    excerpt, truncated, strategy, source_bytes, truncated_chars = support_bundle_module._build_log_excerpt("abc", 0)
    assert excerpt == ""
    assert truncated is True
    assert strategy == "empty"
    assert source_bytes == 3
    assert truncated_chars == 3

    excerpt, truncated, strategy, _bytes, truncated_chars = support_bundle_module._build_log_excerpt("abcdef", 20)
    assert excerpt == "abcdef"
    assert truncated is False
    assert strategy == "full"
    assert truncated_chars == 0

    excerpt, truncated, strategy, _bytes, truncated_chars = support_bundle_module._build_log_excerpt("abcdef", 2)
    assert excerpt == "ef"
    assert truncated is True
    assert strategy == "tail_only"
    assert truncated_chars == 4

    long_log = "A" * 50 + "TAIL"
    excerpt, truncated, strategy, _bytes, _chars = support_bundle_module._build_log_excerpt(long_log, 30)
    assert truncated is True
    assert strategy == "head_tail"
    assert "[TRUNCATED]" in excerpt
    assert "TAIL" in excerpt


def test_build_completeness_warnings_detects_gaps() -> None:
    prom_results = {
        "targets_up_api": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_scheduler": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_worker": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_agent": {"data": {"result": [{"value": [1, "1"]}]}},
        "jobs_started_2h": {"data": {"result": [{"value": [1, "2"]}]}},
        "job_duration_samples_2h": {"data": {"result": [{"value": [1, "0"]}]}},
        "job_queue_wait_samples_2h": {"data": {"result": [{"value": [1, "0"]}]}},
        "jobs_active": {"error": "boom"},
        "job_queue_depth": {"error": "boom"},
    }

    loki_service_logs = {
        "api": {"data": {"result": []}},
        "worker": {"error": "worker down"},
        "scheduler": {"data": {"result": []}},
        "agent": {"data": {"result": []}},
    }

    control_plane = {
        "api": {"error": "api health down"},
        "scheduler": {"payload": {"status": "degraded"}},
        "worker": {"probe": {"ok": False}},
    }

    warnings = support_bundle_module._build_completeness_warnings(
        prom_results=prom_results,
        prom_targets={"error": "targets failed"},
        prom_alerts={"error": "alerts failed"},
        loki_service_logs=loki_service_logs,
        loki_service_label_values=["api", "worker"],
        control_plane_health=control_plane,
    )

    joined = "\n".join(warnings)
    assert "failed to query Prometheus signal 'jobs_active'" in joined
    assert "failed to query Prometheus targets snapshot" in joined
    assert "jobs started in 2h window but no archetype_job_duration_seconds samples" in joined
    assert "no Loki log entries found for service 'api'" in joined
    assert "failed to query Loki entries for service 'worker'" in joined
    assert "api /healthz unavailable" in joined
    assert "scheduler /healthz reported status 'degraded'" in joined
    assert "worker /metrics probe unhealthy" in joined


@pytest.mark.asyncio
async def test_query_prometheus_and_http_helpers(monkeypatch) -> None:
    called: list[tuple[str, dict | None]] = []

    def _handler(url, params):
        called.append((url, params))
        if "label" in url:
            return _DummyResponse(payload={"data": ["api", "worker"]})
        if "healthz" in url:
            return _DummyResponse(payload={"status": "ok"})
        if "metrics" in url:
            return _DummyResponse(status_code=503, payload={}, text="a\nb", headers={"content-type": "text/plain"})
        return _DummyResponse(payload={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(
        support_bundle_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_handler),
    )

    # _query_prometheus wraps _query_prometheus_api and includes eval time param.
    payload = await support_bundle_module._query_prometheus(
        "up",
        evaluation_time=datetime(2026, 3, 1, 11, 59, tzinfo=timezone.utc),
    )
    assert payload["status"] == "success"
    assert called[0][1] is not None and "time" in called[0][1]

    payload = await support_bundle_module._query_prometheus_api("/api/v1/query", params={"query": "up"})
    assert payload["status"] == "success"

    labels = await support_bundle_module._query_loki_label_values("service")
    assert labels["data"] == ["api", "worker"]

    health = await support_bundle_module._query_service_health("http://scheduler/healthz")
    assert health["status"] == "ok"

    probe = await support_bundle_module._probe_http_endpoint("http://worker/metrics")
    assert probe["status_code"] == 503
    assert probe["ok"] is False
    assert probe["line_count"] == 2


@pytest.mark.asyncio
async def test_query_loki_service_logs_branching(monkeypatch) -> None:
    # First selector fails, second returns logs -> early success.
    calls = {"count": 0}

    def _handler_success_then_logs(url, params):
        calls["count"] += 1
        query = params["query"]
        if query.startswith('{service='):
            return RuntimeError("service selector failed")
        if query.startswith('{compose_service='):
            return _DummyResponse(payload={"status": "success", "data": {"result": [{"values": [["1", "a"]]}]}})
        return _DummyResponse(payload={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(
        support_bundle_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_handler_success_then_logs),
    )

    payload = await support_bundle_module._query_loki_service_logs("api", since_hours=1)
    assert payload["selector"] == "compose_service"
    assert payload["attempts"]
    assert calls["count"] >= 2

    # First success has zero logs -> returns first_success after trying all selectors.
    def _handler_first_success_zero(url, params):
        query = params["query"]
        if query.startswith('{service='):
            return _DummyResponse(payload={"status": "success", "data": {"result": []}})
        return RuntimeError("later selectors failed")

    monkeypatch.setattr(
        support_bundle_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_handler_first_success_zero),
    )

    payload = await support_bundle_module._query_loki_service_logs("worker", since_hours=1)
    assert payload["selector"] == "service"
    assert len(payload["attempts"]) == 3

    # All selectors fail -> error payload.
    monkeypatch.setattr(
        support_bundle_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(lambda _u, _p: RuntimeError("all failed")),
    )
    payload = await support_bundle_module._query_loki_service_logs("scheduler", since_hours=1)
    assert "error" in payload
    assert len(payload["attempts"]) == 3


@pytest.mark.asyncio
async def test_collect_agent_snapshot_online_and_offline(sample_host, monkeypatch) -> None:
    # Offline path.
    monkeypatch.setattr(support_bundle_module.agent_client, "is_agent_online", lambda _agent: False)
    payload = await support_bundle_module._collect_agent_snapshot(sample_host)
    assert payload["online"] is False
    assert payload["live"]["error"] == "agent offline"

    # Online path with one failing call.
    monkeypatch.setattr(support_bundle_module.agent_client, "is_agent_online", lambda _agent: True)
    monkeypatch.setattr(support_bundle_module.agent_client, "get_agent_lock_status", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(support_bundle_module.agent_client, "get_overlay_status_from_agent", AsyncMock(side_effect=RuntimeError("overlay boom")))
    monkeypatch.setattr(support_bundle_module.agent_client, "get_ovs_status_from_agent", AsyncMock(return_value={"ovs": "ok"}))
    monkeypatch.setattr(support_bundle_module.agent_client, "get_agent_ovs_flows", AsyncMock(return_value={"flows": []}))
    monkeypatch.setattr(support_bundle_module.agent_client, "get_agent_interface_details", AsyncMock(return_value={"ifaces": []}))
    monkeypatch.setattr(support_bundle_module.agent_client, "get_agent_images", AsyncMock(return_value={"images": []}))

    payload = await support_bundle_module._collect_agent_snapshot(sample_host)
    assert payload["online"] is True
    assert payload["live"]["lock_status"]["ok"] is True
    assert "overlay boom" in payload["live"]["overlay_status"]["error"]


def test_lab_export_include_configs(test_db, sample_lab, sample_job) -> None:
    # Backward-compat shim: _lab_export includes is_active for snapshots.
    setattr(models.ConfigSnapshot, "is_active", False)

    now = datetime.now(timezone.utc)
    snap = models.ConfigSnapshot(
        lab_id=sample_lab.id,
        node_name="R1",
        content="hostname R1",
        content_hash="abc123",
        snapshot_type="manual",
        device_kind="ceos",
        created_at=now,
    )
    test_db.add(snap)
    test_db.commit()

    payload = support_bundle_module._lab_export(
        test_db,
        sample_lab,
        since_dt=now - timedelta(hours=1),
        until_dt=now + timedelta(hours=1),
        include_configs=True,
    )

    assert "config_snapshots" in payload
    assert payload["config_snapshots"]
    assert payload["config_snapshots"][0]["content"] == "hostname R1"


@pytest.mark.asyncio
async def test_run_bundle_generation_success_with_warnings(test_db, test_user, monkeypatch, tmp_path) -> None:
    bundle = models.SupportBundle(
        user_id=test_user.id,
        status="pending",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json="{}",
        incident_json="{}",
    )
    test_db.add(bundle)
    test_db.commit()
    test_db.refresh(bundle)

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(support_bundle_module.db, "get_session", _fake_get_session)
    monkeypatch.setattr(support_bundle_module, "_bundle_dir", lambda: Path(tmp_path))
    monkeypatch.setattr(
        support_bundle_module,
        "build_support_bundle",
        AsyncMock(return_value=(b"ZIP", {"manifest": {"errors": ["warn"]}, "archive_size_bytes": 3})),
    )

    await support_bundle_module.run_bundle_generation(bundle.id)

    test_db.refresh(bundle)
    assert bundle.status == "completed_with_warnings"
    assert bundle.file_path is not None and bundle.file_path.endswith(f"{bundle.id}.zip")
    assert bundle.size_bytes == 3
    assert bundle.completed_at is not None
    updated_opts = json.loads(bundle.options_json)
    assert updated_opts["manifest"]["errors"] == ["warn"]


@pytest.mark.asyncio
async def test_run_bundle_generation_failure_marks_failed(test_db, test_user, monkeypatch) -> None:
    bundle = models.SupportBundle(
        user_id=test_user.id,
        status="pending",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json="{}",
        incident_json="{}",
    )
    test_db.add(bundle)
    test_db.commit()
    test_db.refresh(bundle)

    @contextmanager
    def _fake_get_session():
        yield test_db

    monkeypatch.setattr(support_bundle_module.db, "get_session", _fake_get_session)
    monkeypatch.setattr(
        support_bundle_module,
        "build_support_bundle",
        AsyncMock(side_effect=RuntimeError("bundle exploded")),
    )

    await support_bundle_module.run_bundle_generation(bundle.id)

    test_db.refresh(bundle)
    assert bundle.status == "failed"
    assert "bundle exploded" in (bundle.error_message or "")
    assert bundle.completed_at is not None


@pytest.mark.asyncio
async def test_build_support_bundle_writes_boot_logs_when_available(
    test_db,
    test_user,
    sample_lab,
    sample_host,
    monkeypatch,
) -> None:
    sample_lab.agent_id = sample_host.id
    test_db.add(sample_lab)
    test_db.commit()

    bundle = models.SupportBundle(
        user_id=test_user.id,
        status="running",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json=json.dumps({"impacted_lab_ids": [sample_lab.id], "impacted_agent_ids": [sample_host.id]}),
        incident_json=json.dumps({"summary": "x", "repro_steps": "y", "expected_behavior": "z", "actual_behavior": "w"}),
    )

    monkeypatch.setattr(support_bundle_module, "_query_prometheus", AsyncMock(return_value={"status": "success", "data": {"result": []}}))
    monkeypatch.setattr(support_bundle_module, "_query_prometheus_targets", AsyncMock(return_value={"status": "success", "data": {"activeTargets": []}}))
    monkeypatch.setattr(support_bundle_module, "_query_prometheus_alerts", AsyncMock(return_value={"status": "success", "data": {"alerts": []}}))
    monkeypatch.setattr(support_bundle_module, "_query_loki_service_logs", AsyncMock(return_value={"status": "success", "data": {"result": []}}))
    monkeypatch.setattr(support_bundle_module, "_query_loki_label_values", AsyncMock(return_value={"status": "success", "data": ["api"]}))
    monkeypatch.setattr(support_bundle_module, "_query_service_health", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(
        support_bundle_module,
        "_probe_http_endpoint",
        AsyncMock(return_value={
            "status_code": 200,
            "ok": True,
            "content_type": "text/plain",
            "content_length": 1,
            "line_count": 1,
        }),
    )
    monkeypatch.setattr(
        support_bundle_module,
        "_collect_agent_snapshot",
        AsyncMock(return_value={"id": sample_host.id, "online": True, "live": {}}),
    )

    monkeypatch.setattr(support_bundle_module.agent_client, "is_agent_online", lambda _h: True)
    monkeypatch.setattr(
        support_bundle_module.agent_client,
        "get_agent_boot_logs",
        AsyncMock(return_value={"boot_logs": {"r1": "boot line"}}),
    )
    monkeypatch.setattr(
        support_bundle_module.agent_client,
        "get_agent_url",
        lambda host: f"http://{host.address}",
    )

    archive, _metadata = await support_bundle_module.build_support_bundle(test_db, bundle)

    with zipfile.ZipFile(BytesIO(archive), "r") as zf:
        assert f"labs/{sample_lab.id}/boot-logs-{sample_host.id}.json" in zf.namelist()


def test_resolve_worker_metrics_urls_raw_and_wrapper_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        support_bundle_module,
        "WORKER_METRICS_URLS_RAW",
        " http://worker-a:8003/metrics , http://worker-a:8003/metrics ,http://worker-b:8003/metrics ",
    )
    monkeypatch.setattr(support_bundle_module, "WORKER_METRICS_URL", "http://worker-default:8003/metrics")

    urls = support_bundle_module._resolve_worker_metrics_urls()
    assert urls[0] == "http://worker-a:8003/metrics"
    assert "http://worker-b:8003/metrics" in urls
    assert "http://worker-default:8003/metrics" in urls
    assert urls.count("http://worker-a:8003/metrics") == 1


def test_support_bundle_parser_edge_lines(monkeypatch) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(support_bundle_module, "_now_utc", lambda: now)

    # Future-only incident window should clamp until_dt back to "now".
    since, until = support_bundle_module._resolve_collection_window(
        {"incident_started_at": "2026-03-01T20:00:00Z"},
        2,
    )
    assert until == now
    assert since == now - timedelta(hours=2)

    # Row and value-shape guards in Prometheus sum parser.
    total = support_bundle_module._prometheus_scalar_sum(
        {"data": {"result": ["not-a-dict", {"value": {"unexpected": "shape"}}, {"value": [1, "1.5"]}]}}
    )
    assert total == 1.5

    # Non-dict stream rows are skipped in Loki count parser.
    count = support_bundle_module._loki_entry_count(
        {"data": {"result": ["bad-row", {"values": [["1", "ok"]]}]}}
    )
    assert count == 1


@pytest.mark.asyncio
async def test_wrapper_queries_and_non_dict_loki_payload(monkeypatch) -> None:
    real_query_loki_service_logs = support_bundle_module._query_loki_service_logs

    monkeypatch.setattr(
        support_bundle_module,
        "_query_prometheus_api",
        AsyncMock(return_value={"status": "success", "data": {}}),
    )
    monkeypatch.setattr(
        support_bundle_module,
        "_query_loki_service_logs",
        AsyncMock(return_value={"status": "success", "service": "api"}),
    )

    targets = await support_bundle_module._query_prometheus_targets()
    alerts = await support_bundle_module._query_prometheus_alerts()
    loki_api = await support_bundle_module._query_loki_api_logs(2)
    assert targets["status"] == "success"
    assert alerts["status"] == "success"
    assert loki_api["service"] == "api"

    # Non-dict Loki payload should be normalized to an empty success payload.
    def _handler(_url, _params):
        return _DummyResponse(payload=[])

    monkeypatch.setattr(
        support_bundle_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(_handler),
    )
    payload = await real_query_loki_service_logs("api", since_hours=1)
    assert payload["status"] == "success"
    assert payload["data"]["result"] == []


def test_completeness_warning_health_edge_branches() -> None:
    base_prom = {
        "targets_up_api": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_scheduler": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_worker": {"data": {"result": [{"value": [1, "1"]}]}},
        "targets_up_agent": {"data": {"result": [{"value": [1, "1"]}]}},
        "jobs_started_2h": {"data": {"result": [{"value": [1, "0"]}]}},
        "job_duration_samples_2h": {"data": {"result": [{"value": [1, "0"]}]}},
        "job_queue_wait_samples_2h": {"data": {"result": [{"value": [1, "0"]}]}},
        "jobs_active": {"data": {"result": []}},
        "job_queue_depth": {"data": {"result": []}},
    }

    warn1 = support_bundle_module._build_completeness_warnings(
        prom_results=base_prom,
        prom_targets={},
        prom_alerts={},
        loki_service_logs={"api": {}, "worker": {}, "scheduler": {}, "agent": {}},
        loki_service_label_values=[],
        control_plane_health={"api": None, "scheduler": None, "worker": None},
    )
    joined1 = "\n".join(warn1)
    assert "api /healthz snapshot missing" in joined1
    assert "scheduler /healthz snapshot missing" in joined1
    assert "worker /metrics probe missing" in joined1

    warn2 = support_bundle_module._build_completeness_warnings(
        prom_results=base_prom,
        prom_targets={},
        prom_alerts={},
        loki_service_logs={"api": {}, "worker": {}, "scheduler": {}, "agent": {}},
        loki_service_label_values=[],
        control_plane_health={
            "api": {"error": "boom"},
            "scheduler": {"error": "boom"},
            "worker": {"probe": "invalid"},
        },
    )
    joined2 = "\n".join(warn2)
    assert "api /healthz unavailable" in joined2
    assert "scheduler /healthz unavailable" in joined2
    assert "worker /metrics probe payload missing" in joined2

    warn3 = support_bundle_module._build_completeness_warnings(
        prom_results=base_prom,
        prom_targets={},
        prom_alerts={},
        loki_service_logs={"api": {}, "worker": {}, "scheduler": {}, "agent": {}},
        loki_service_label_values=[],
        control_plane_health={
            "api": {"payload": {"status": "ok"}},
            "scheduler": {"payload": {}},
            "worker": {"probe": {"error": "down"}},
        },
    )
    joined3 = "\n".join(warn3)
    assert "scheduler /healthz payload missing status" in joined3
    assert "worker /metrics probe unavailable" in joined3


@pytest.mark.asyncio
async def test_build_support_bundle_default_selection_and_error_accumulation(
    test_db,
    test_user,
    sample_lab,
    monkeypatch,
) -> None:
    host_a = models.Host(
        id="bundle-host-a",
        name="bundle-host-a",
        address="10.0.0.1:8080",
        status="online",
        capabilities="{}",
        resource_usage="{}",
        version="1.0",
        last_heartbeat=datetime.now(timezone.utc),
    )
    host_b = models.Host(
        id="bundle-host-b",
        name="bundle-host-b",
        address="10.0.0.2:8080",
        status="online",
        capabilities="{}",
        resource_usage="{}",
        version="1.0",
        last_heartbeat=datetime.now(timezone.utc),
    )
    sample_lab.agent_id = host_a.id
    test_db.add_all([host_a, host_b, sample_lab])
    test_db.add(models.NodePlacement(lab_id=sample_lab.id, node_name="r1", host_id=host_b.id, status="deployed"))
    test_db.commit()

    bundle = models.SupportBundle(
        user_id=test_user.id,
        status="running",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json=json.dumps({}),  # no impacted IDs => default lab selection path
        incident_json=json.dumps({"summary": "x"}),
    )

    monkeypatch.setattr(support_bundle_module, "MAX_AGENT_HEALTH_PROBES", 1)

    async def _fake_query_prom(expr: str, **_kwargs) -> dict:
        if "archetype_circuit_breaker_state" in expr:
            return {
                "data": {
                    "result": [
                        {"metric": {"handler_type": "h-bad", "job": "sched"}, "value": [1, "bad"]},
                        {"metric": {"handler_type": "h-half", "job": "sched"}, "value": [1, "1"]},
                        {"metric": {"handler_type": "h-closed", "job": "sched"}, "value": [1, "0"]},
                    ]
                }
            }
        return {"status": "success", "data": {"result": []}}

    monkeypatch.setattr(support_bundle_module, "_query_prometheus", _fake_query_prom)
    monkeypatch.setattr(support_bundle_module, "_query_prometheus_targets", AsyncMock(return_value={"status": "success", "data": {}}))
    monkeypatch.setattr(support_bundle_module, "_query_prometheus_alerts", AsyncMock(return_value={"status": "success", "data": {}}))
    monkeypatch.setattr(support_bundle_module, "_query_loki_service_logs", AsyncMock(return_value={"status": "success", "data": {"result": []}}))
    monkeypatch.setattr(support_bundle_module, "_query_loki_label_values", AsyncMock(side_effect=RuntimeError("label query failed")))

    async def _fake_service_health(url: str, **_kwargs) -> dict:
        if "scheduler" in url or "10.0.0.1" in url or "10.0.0.2" in url:
            raise RuntimeError("health unavailable")
        return {"status": "ok"}

    monkeypatch.setattr(support_bundle_module, "_query_service_health", _fake_service_health)
    monkeypatch.setattr(
        support_bundle_module,
        "_probe_http_endpoint",
        AsyncMock(return_value={"status_code": 503, "ok": False, "content_type": "text/plain", "content_length": 0, "line_count": 0}),
    )
    monkeypatch.setattr(support_bundle_module.agent_client, "is_agent_online", lambda _h: True)
    monkeypatch.setattr(support_bundle_module.agent_client, "get_agent_url", lambda host: f"http://{host.address}")
    monkeypatch.setattr(
        support_bundle_module.agent_client,
        "get_agent_boot_logs",
        AsyncMock(side_effect=RuntimeError("boot logs unavailable")),
    )

    async def _collect_snapshot(host):
        if host.id == "bundle-host-a":
            raise RuntimeError("snapshot failed")
        return {"id": host.id, "online": True}

    monkeypatch.setattr(support_bundle_module, "_collect_agent_snapshot", _collect_snapshot)
    monkeypatch.setattr(
        support_bundle_module,
        "_lab_export",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("lab export failed")),
    )

    import app.db as db_module

    monkeypatch.setattr(db_module, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("redis unavailable")))

    archive, _metadata = await support_bundle_module.build_support_bundle(test_db, bundle)
    with zipfile.ZipFile(BytesIO(archive), "r") as zf:
        control_plane = json.loads(zf.read("system/control-plane-health.json"))
        assert control_plane["agent"]["sample_count"] == 1
        assert control_plane["agent"]["omitted_count"] >= 1
        cb_state = json.loads(zf.read("system/circuit-breaker.json"))
        assert cb_state["handlers"]["h-half"]["state"] == "half_open"
        assert cb_state["handlers"]["h-closed"]["state"] == "closed"
        errors = json.loads(zf.read("errors.json"))["errors"]
        assert any("lab export failed" in err for err in errors)
        assert any("snapshot failed" in err for err in errors)


@pytest.mark.asyncio
async def test_run_bundle_generation_missing_bundle_paths(test_db, test_user, monkeypatch, tmp_path) -> None:
    # Missing bundle on first lookup => early return.
    @contextmanager
    def _first_missing_session():
        yield test_db

    monkeypatch.setattr(support_bundle_module.db, "get_session", _first_missing_session)
    await support_bundle_module.run_bundle_generation("does-not-exist")

    # Missing bundle on second lookup after first phase.
    bundle = models.SupportBundle(
        user_id=test_user.id,
        status="pending",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json="{}",
        incident_json="{}",
    )
    test_db.add(bundle)
    test_db.commit()
    test_db.refresh(bundle)

    calls = {"n": 0}

    @contextmanager
    def _missing_on_second():
        calls["n"] += 1
        if calls["n"] == 2:
            b = test_db.get(models.SupportBundle, bundle.id)
            if b is not None:
                test_db.delete(b)
                test_db.commit()
        yield test_db

    monkeypatch.setattr(support_bundle_module.db, "get_session", _missing_on_second)
    monkeypatch.setattr(support_bundle_module, "_bundle_dir", lambda: Path(tmp_path))
    monkeypatch.setattr(
        support_bundle_module,
        "build_support_bundle",
        AsyncMock(return_value=(b"ZIP", {"manifest": {}, "archive_size_bytes": 3})),
    )
    await support_bundle_module.run_bundle_generation(bundle.id)

    # Missing bundle in exception recovery path.
    bundle2 = models.SupportBundle(
        user_id=test_user.id,
        status="pending",
        include_configs=False,
        pii_safe=True,
        time_window_hours=24,
        options_json="{}",
        incident_json="{}",
    )
    test_db.add(bundle2)
    test_db.commit()
    test_db.refresh(bundle2)

    calls2 = {"n": 0}

    @contextmanager
    def _missing_on_except():
        calls2["n"] += 1
        if calls2["n"] == 3:
            b = test_db.get(models.SupportBundle, bundle2.id)
            if b is not None:
                test_db.delete(b)
                test_db.commit()
        yield test_db

    monkeypatch.setattr(support_bundle_module.db, "get_session", _missing_on_except)
    monkeypatch.setattr(
        support_bundle_module,
        "build_support_bundle",
        AsyncMock(side_effect=RuntimeError("explode after start")),
    )
    await support_bundle_module.run_bundle_generation(bundle2.id)
