"""Support bundle generation service."""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app import agent_client, db, models
from app.config import settings
from app.services.topology import TopologyService
from app.storage import read_layout
from app.utils.logs import get_log_content

MAX_BUNDLE_BYTES = 200 * 1024 * 1024
MAX_LOG_BYTES_PER_JOB = 50_000
MAX_JOBS_PER_LAB = 100

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|authorization|api[_-]?key|private[_-]?key|cookie|session)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL),
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(data: Any) -> bytes:
    return json.dumps(data, indent=2, sort_keys=True, default=str).encode("utf-8")


def _safe_json_load(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {"raw": value}


def _model_to_dict(instance: Any, fields: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in fields:
        value = getattr(instance, field, None)
        if isinstance(value, datetime):
            out[field] = value.isoformat()
        else:
            out[field] = value
    return out


def _hash_alias(prefix: str, source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


class ZipBuilder:
    """Track bundle size and checksums while writing zip content."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.total_input_bytes = 0
        self.errors: list[str] = []
        self.files: list[dict[str, Any]] = []
        self._buffer = BytesIO()
        self._zip = zipfile.ZipFile(self._buffer, "w", compression=zipfile.ZIP_DEFLATED)

    def add_bytes(self, path: str, content: bytes) -> bool:
        size = len(content)
        if self.total_input_bytes + size > self.max_bytes:
            self.errors.append(f"Skipped {path}: bundle size cap would be exceeded")
            return False
        self._zip.writestr(path, content)
        self.total_input_bytes += size
        self.files.append(
            {
                "path": path,
                "size_bytes": size,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        return True

    def add_json(self, path: str, payload: Any) -> bool:
        return self.add_bytes(path, _json_dumps(payload))

    def close(self) -> bytes:
        self._zip.close()
        return self._buffer.getvalue()


def _redact_string(value: str, *, pii_safe: bool) -> str:
    text = value
    for pattern in SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if pii_safe:
        text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[MASKED_EMAIL]", text)
    return text


def sanitize_data(value: Any, *, pii_safe: bool, lab_alias: dict[str, str], host_alias: dict[str, str]) -> Any:
    """Redact sensitive keys/values and mask known PII identifiers."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if SENSITIVE_KEY_PATTERN.search(key_str):
                out[key_str] = "[REDACTED]"
                continue
            out[key_str] = sanitize_data(
                item,
                pii_safe=pii_safe,
                lab_alias=lab_alias,
                host_alias=host_alias,
            )
        return out

    if isinstance(value, list):
        return [
            sanitize_data(item, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias)
            for item in value
        ]

    if isinstance(value, str):
        text = _redact_string(value, pii_safe=pii_safe)
        if pii_safe:
            for src, alias in lab_alias.items():
                text = text.replace(src, alias)
            for src, alias in host_alias.items():
                text = text.replace(src, alias)
        return text

    return value


def _bundle_dir() -> Path:
    path = Path(settings.workspace) / "support-bundles"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _query_prometheus(expr: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": expr},
        )
        resp.raise_for_status()
        return resp.json()


async def _query_loki_api_logs(since_hours: int, limit: int = 500) -> dict[str, Any]:
    seconds = max(1, since_hours) * 3600
    start_ns = (int(_now_utc().timestamp()) - seconds) * 1_000_000_000
    query = '{service="api"} | json'
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.get(
            f"{settings.loki_url}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": start_ns,
                "limit": limit,
                "direction": "backward",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _collect_agent_snapshot(agent: models.Host) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "id": agent.id,
        "name": agent.name,
        "address": agent.address,
        "status": agent.status,
        "version": agent.version,
        "git_sha": agent.git_sha,
        "last_heartbeat": agent.last_heartbeat.isoformat() if agent.last_heartbeat else None,
        "resource_usage": _safe_json_load(agent.resource_usage),
        "capabilities": _safe_json_load(agent.capabilities),
    }

    if not agent_client.is_agent_online(agent):
        snapshot["online"] = False
        snapshot["live"] = {"error": "agent offline"}
        return snapshot

    snapshot["online"] = True
    live: dict[str, Any] = {}
    for key, func in (
        ("lock_status", agent_client.get_agent_lock_status),
        ("overlay_status", agent_client.get_overlay_status_from_agent),
        ("ovs_status", agent_client.get_ovs_status_from_agent),
        ("interface_details", agent_client.get_agent_interface_details),
        ("images", agent_client.get_agent_images),
    ):
        try:
            live[key] = await func(agent)
        except Exception as exc:
            live[key] = {"error": str(exc)}
    snapshot["live"] = live
    return snapshot


def _lab_export(
    session: Session,
    lab: models.Lab,
    since_dt: datetime,
    include_configs: bool,
) -> dict[str, Any]:
    service = TopologyService(session)
    has_topology = service.has_nodes(lab.id)

    topology_yaml = service.export_to_yaml(lab.id) if has_topology else "nodes: {}\nlinks: []\n"
    topology_graph = (
        service.export_to_graph(lab.id).model_dump(mode="json")
        if has_topology
        else {"nodes": [], "links": []}
    )
    layout = read_layout(lab.id)
    layout_json = layout.model_dump(mode="json") if layout else None

    node_states = (
        session.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab.id)
        .order_by(models.NodeState.node_name.asc())
        .all()
    )
    link_states = (
        session.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab.id)
        .order_by(models.LinkState.link_name.asc())
        .all()
    )
    placements = (
        session.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab.id)
        .all()
    )
    jobs = (
        session.query(models.Job)
        .filter(models.Job.lab_id == lab.id, models.Job.created_at >= since_dt)
        .order_by(models.Job.created_at.desc())
        .limit(MAX_JOBS_PER_LAB)
        .all()
    )

    job_exports: list[dict[str, Any]] = []
    for job in jobs:
        raw_log = get_log_content(job.log_path) or ""
        log_excerpt = raw_log[:MAX_LOG_BYTES_PER_JOB]
        job_exports.append(
            {
                "id": job.id,
                "action": job.action,
                "status": job.status,
                "agent_id": job.agent_id,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "retry_count": job.retry_count,
                "log_excerpt": log_excerpt,
                "log_truncated": len(raw_log) > len(log_excerpt),
            }
        )

    export: dict[str, Any] = {
        "metadata": {
            "id": lab.id,
            "name": lab.name,
            "state": lab.state,
            "state_error": lab.state_error,
            "state_updated_at": lab.state_updated_at.isoformat() if lab.state_updated_at else None,
            "created_at": lab.created_at.isoformat() if lab.created_at else None,
        },
        "topology_yaml": topology_yaml,
        "topology_graph": topology_graph,
        "layout": layout_json,
        "node_states": [
            _model_to_dict(
                n,
                [
                    "id",
                    "lab_id",
                    "node_id",
                    "node_name",
                    "desired_state",
                    "actual_state",
                    "error_message",
                    "is_ready",
                    "boot_started_at",
                    "starting_started_at",
                    "stopping_started_at",
                    "image_sync_status",
                    "image_sync_message",
                    "management_ip",
                    "management_ips_json",
                    "enforcement_attempts",
                    "last_enforcement_at",
                    "enforcement_failed_at",
                    "created_at",
                    "updated_at",
                ],
            )
            for n in node_states
        ],
        "link_states": [
            _model_to_dict(
                link_state,
                [
                    "id",
                    "lab_id",
                    "link_name",
                    "source_node",
                    "source_interface",
                    "target_node",
                    "target_interface",
                    "desired_state",
                    "actual_state",
                    "error_message",
                    "is_cross_host",
                    "vni",
                    "vlan_tag",
                    "source_host_id",
                    "target_host_id",
                    "source_vxlan_attached",
                    "target_vxlan_attached",
                    "created_at",
                    "updated_at",
                ],
            )
            for link_state in link_states
        ],
        "placements": [
            _model_to_dict(
                p,
                [
                    "id",
                    "lab_id",
                    "node_name",
                    "node_definition_id",
                    "host_id",
                    "runtime_id",
                    "status",
                    "created_at",
                ],
            )
            for p in placements
        ],
        "jobs": job_exports,
    }

    if include_configs:
        configs = (
            session.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab.id)
            .order_by(models.ConfigSnapshot.created_at.desc())
            .limit(200)
            .all()
        )
        export["config_snapshots"] = [
            {
                "id": c.id,
                "node_name": c.node_name,
                "snapshot_type": c.snapshot_type,
                "device_kind": c.device_kind,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "is_active": c.is_active,
                "content": c.content,
            }
            for c in configs
        ]

    return export


async def build_support_bundle(
    session: Session,
    bundle: models.SupportBundle,
) -> tuple[bytes, dict[str, Any]]:
    options = _safe_json_load(bundle.options_json)
    incident = _safe_json_load(bundle.incident_json)
    time_window_hours = max(1, min(int(bundle.time_window_hours or 24), 168))
    since_dt = _now_utc() - timedelta(hours=time_window_hours)
    pii_safe = bool(bundle.pii_safe)
    include_configs = bool(bundle.include_configs)

    lab_ids: list[str] = list(options.get("impacted_lab_ids") or [])
    agent_ids: list[str] = list(options.get("impacted_agent_ids") or [])

    selected_labs: list[models.Lab] = []
    if lab_ids:
        selected_labs = session.query(models.Lab).filter(models.Lab.id.in_(lab_ids)).all()
    else:
        selected_labs = (
            session.query(models.Lab)
            .order_by(models.Lab.created_at.desc())
            .limit(10)
            .all()
        )

    hosts_from_labs = {
        placement.host_id
        for lab in selected_labs
        for placement in session.query(models.NodePlacement).filter(models.NodePlacement.lab_id == lab.id).all()
        if placement.host_id
    }
    agent_ids = sorted({*agent_ids, *hosts_from_labs, *(lab.agent_id for lab in selected_labs if lab.agent_id)})
    selected_agents = session.query(models.Host).filter(models.Host.id.in_(agent_ids)).all() if agent_ids else []

    lab_alias = {lab.name: _hash_alias("lab", lab.id) for lab in selected_labs if lab.name}
    host_alias = {host.name: _hash_alias("host", host.id) for host in selected_agents if host.name}

    writer = ZipBuilder(MAX_BUNDLE_BYTES)

    # Incident report is required context from user.
    writer.add_json(
        "incident/user-report.json",
        sanitize_data(incident, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias),
    )

    system_info = {
        "generated_at": _now_utc().isoformat(),
        "bundle_id": bundle.id,
        "time_window_hours": time_window_hours,
        "service": "archetype-api",
        "api_settings": {
            "provider": settings.provider,
            "log_format": settings.log_format,
            "log_level": settings.log_level,
            "cleanup_job_retention_days": settings.cleanup_job_retention_days,
            "cleanup_config_snapshot_retention_days": settings.cleanup_config_snapshot_retention_days,
            "state_enforcement_enabled": settings.state_enforcement_enabled,
            "image_sync_enabled": settings.image_sync_enabled,
            "feature_multihost_labs": settings.feature_multihost_labs,
        },
    }
    writer.add_json(
        "system/controller.json",
        sanitize_data(system_info, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias),
    )

    # API action logs from DB-backed jobs and audit events.
    audit_logs = (
        session.query(models.AuditLog)
        .filter(models.AuditLog.created_at >= since_dt)
        .order_by(models.AuditLog.created_at.desc())
        .limit(2000)
        .all()
    )
    api_jobs = (
        session.query(models.Job)
        .filter(models.Job.created_at >= since_dt)
        .order_by(models.Job.created_at.desc())
        .limit(3000)
        .all()
    )
    writer.add_json(
        "api/action-logs.json",
        sanitize_data(
            {
                "audit_logs": [
                    {
                        "id": a.id,
                        "event_type": a.event_type,
                        "user_id": a.user_id,
                        "target_user_id": a.target_user_id,
                        "ip_address": a.ip_address,
                        "details": _safe_json_load(a.details_json),
                        "created_at": a.created_at.isoformat() if a.created_at else None,
                    }
                    for a in audit_logs
                ],
                "jobs": [
                    {
                        "id": j.id,
                        "lab_id": j.lab_id,
                        "user_id": j.user_id,
                        "action": j.action,
                        "status": j.status,
                        "agent_id": j.agent_id,
                        "created_at": j.created_at.isoformat() if j.created_at else None,
                        "started_at": j.started_at.isoformat() if j.started_at else None,
                        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    }
                    for j in api_jobs
                ],
            },
            pii_safe=pii_safe,
            lab_alias=lab_alias,
            host_alias=host_alias,
        ),
    )

    # Lab artifacts and runtime state.
    for lab in selected_labs:
        try:
            lab_payload = _lab_export(session, lab, since_dt, include_configs=include_configs)
            payload = sanitize_data(lab_payload, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias)
            writer.add_json(f"labs/{lab.id}/bundle.json", payload)
        except Exception as exc:
            writer.errors.append(f"Failed lab export for {lab.id}: {exc}")

    # Agent snapshots.
    for host in selected_agents:
        try:
            agent_payload = await _collect_agent_snapshot(host)
            payload = sanitize_data(agent_payload, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias)
            writer.add_json(f"agents/{host.id}/snapshot.json", payload)
        except Exception as exc:
            writer.errors.append(f"Failed agent snapshot for {host.id}: {exc}")

    # Observability snapshots (best-effort).
    prom_queries = {
        "agents_online": "archetype_agents_online",
        "jobs_started_2h": 'sum(increase(archetype_jobs_total{status="started"}[2h]))',
        "jobs_failed_2h": 'sum(increase(archetype_jobs_total{status="failed"}[2h]))',
        "jobs_active": "sum(archetype_jobs_active)",
        "nlm_samples_2h": "sum(increase(archetype_nlm_phase_duration_seconds_count[2h]))",
    }
    prom_results: dict[str, Any] = {}
    for name, expr in prom_queries.items():
        try:
            prom_results[name] = await _query_prometheus(expr)
        except Exception as exc:
            prom_results[name] = {"error": str(exc)}
    writer.add_json(
        "observability/prometheus.json",
        sanitize_data(prom_results, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias),
    )

    try:
        loki_logs = await _query_loki_api_logs(time_window_hours)
    except Exception as exc:
        loki_logs = {"error": str(exc)}
    writer.add_json(
        "observability/loki-api-logs.json",
        sanitize_data(loki_logs, pii_safe=pii_safe, lab_alias=lab_alias, host_alias=host_alias),
    )

    redaction_report = {
        "pii_safe": pii_safe,
        "masked_lab_names": bool(pii_safe),
        "masked_host_names": bool(pii_safe),
        "rules": [
            "Sensitive keys: password/secret/token/api_key/private_key/cookie/session",
            "Sensitive values: bearer tokens, inline secrets, private key blocks",
            "PII-safe mode masks hostnames/lab names and email addresses",
        ],
        "lab_aliases": lab_alias if pii_safe else {},
        "host_aliases": host_alias if pii_safe else {},
    }
    writer.add_json("redaction-report.json", redaction_report)

    if writer.errors:
        writer.add_json("errors.json", {"errors": writer.errors})

    manifest = {
        "bundle_id": bundle.id,
        "generated_at": _now_utc().isoformat(),
        "size_cap_bytes": MAX_BUNDLE_BYTES,
        "include_configs": include_configs,
        "pii_safe": pii_safe,
        "time_window_hours": time_window_hours,
        "files": writer.files,
        "errors": writer.errors,
        "input_bytes": writer.total_input_bytes,
    }
    writer.add_json("manifest.json", manifest)

    archive = writer.close()
    metadata = {
        "manifest": manifest,
        "archive_size_bytes": len(archive),
    }
    return archive, metadata


async def run_bundle_generation(bundle_id: str) -> None:
    """Background task entrypoint for support bundle generation."""
    with db.get_session() as session:
        bundle = session.get(models.SupportBundle, bundle_id)
        if bundle is None:
            return
        bundle.status = "running"
        bundle.started_at = _now_utc()
        bundle.error_message = None
        session.commit()

    try:
        with db.get_session() as session:
            bundle = session.get(models.SupportBundle, bundle_id)
            if bundle is None:
                return
            archive, metadata = await build_support_bundle(session, bundle)

            output_path = _bundle_dir() / f"{bundle_id}.zip"
            output_path.write_bytes(archive)

            bundle.status = "completed"
            bundle.file_path = str(output_path)
            bundle.size_bytes = len(archive)
            bundle.completed_at = _now_utc()
            # Store final manifest and export metadata for quick listing/debugging.
            bundle.options_json = json.dumps(
                {
                    **(_safe_json_load(bundle.options_json) or {}),
                    "manifest": metadata.get("manifest", {}),
                    "archive_size_bytes": metadata.get("archive_size_bytes"),
                }
            )
            session.commit()
    except Exception as exc:
        with db.get_session() as session:
            bundle = session.get(models.SupportBundle, bundle_id)
            if bundle is None:
                return
            bundle.status = "failed"
            bundle.error_message = str(exc)
            bundle.completed_at = _now_utc()
            session.commit()
