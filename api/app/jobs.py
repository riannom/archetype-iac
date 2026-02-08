from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
from redis import Redis
from rq import Queue
from sqlalchemy import or_

from app.config import settings
from app.db import get_session
from app.models import Job, Lab
from app.netlab import run_netlab_command
from app.providers import ProviderActionError, node_action_command
from app.storage import lab_workspace

redis_conn = Redis.from_url(settings.redis_url)
queue = Queue("archetype", connection=redis_conn)


# Actions that conflict with each other for concurrent execution
CONFLICTING_ACTIONS = {
    "up": ["up", "down", "sync"],
    "down": ["up", "down", "sync"],
    "sync": ["up", "down"],
}


def has_conflicting_job(lab_id: str, action: str, session=None) -> tuple[bool, str | None]:
    """Check if lab has a running/queued job that conflicts with new action.

    Args:
        lab_id: The lab ID to check
        action: The action being attempted (up, down, sync, etc.)
        session: Optional SQLAlchemy session to use. If provided, uses that
            session (important for transactional consistency with SELECT FOR UPDATE).
            Otherwise creates a new session via get_session().

    Returns:
        Tuple of (has_conflict, conflicting_action_name)
    """
    conflicting_actions = CONFLICTING_ACTIONS.get(action, [])
    if not conflicting_actions:
        return False, None

    # Build OR conditions for both exact and prefix matching.
    # Sync jobs use formats like sync:node:xxx, sync:lab:xxx, sync:batch:N
    # so we need to match both "sync" exactly and "sync:*" prefixes.
    conditions = []
    for action_name in conflicting_actions:
        conditions.append(Job.action == action_name)
        conditions.append(Job.action.like(f"{action_name}:%"))

    if session is not None:
        active_job = (
            session.query(Job)
            .filter(
                Job.lab_id == lab_id,
                Job.status.in_(["queued", "running"]),
                or_(*conditions),
            )
            .first()
        )
        return (True, active_job.action) if active_job else (False, None)

    with get_session() as s:
        active_job = (
            s.query(Job)
            .filter(
                Job.lab_id == lab_id,
                Job.status.in_(["queued", "running"]),
                or_(*conditions),
            )
            .first()
        )
        return (True, active_job.action) if active_job else (False, None)


def _build_command(lab_id: str, action: str) -> list[list[str]]:
    if action.startswith("node:"):
        _, subaction, node = action.split(":", 2)
        try:
            return node_action_command(settings.provider, lab_id, subaction, node)
        except ProviderActionError as exc:
            raise ValueError(str(exc)) from exc
    return [["netlab", action]]


def execute_netlab_action(job_id: str, lab_id: str, action: str) -> None:
    with get_session() as session:
        job_record = session.get(Job, job_id)
        lab = session.get(Lab, lab_id)
        if not job_record or not lab:
            return
        job_record.status = "running"
        session.commit()

        workspace = lab_workspace(lab_id)
        log_path = workspace / f"job-{job_id}.log"
        try:
            commands = _build_command(lab_id, action)
            output_chunks: list[str] = []
            failed = False
            for command in commands:
                output_chunks.append(f"$ {' '.join(command)}\n")
                code, stdout, stderr = run_netlab_command(command, workspace)
                if stdout:
                    output_chunks.append(stdout)
                if stderr:
                    if stdout and not stdout.endswith("\n"):
                        output_chunks.append("\n")
                    output_chunks.append(stderr)
                if code != 0:
                    failed = True
                    break
            log_content = "".join(output_chunks)
            log_path.write_text(log_content, encoding="utf-8")
            job_record.status = "failed" if failed else "completed"
        except Exception as exc:
            log_content = f"Failed to run action {action}: {exc}\n"
            log_path.write_text(log_content, encoding="utf-8")
            job_record.status = "failed"
        job_record.log_path = str(log_path)
        job_record.created_at = job_record.created_at or datetime.now(timezone.utc)
        session.commit()
        if settings.log_forward_url:
            try:
                httpx.post(
                    settings.log_forward_url,
                    json={
                        "job_id": job_id,
                        "lab_id": lab_id,
                        "action": action,
                        "status": job_record.status,
                        "log": log_content,
                        "created_at": job_record.created_at.isoformat(),
                    },
                    timeout=5.0,
                )
            except Exception:
                pass


def enqueue_job(lab_id: str, action: str, user_id: str | None) -> Job:
    with get_session() as session:
        if user_id:
            # Use Redis lock to prevent race condition in concurrency check
            lock_key = f"job_limit_lock:{user_id}"
            lock_acquired = redis_conn.set(lock_key, "1", nx=True, ex=10)
            if not lock_acquired:
                raise ValueError("Concurrency limit reached (lock contention)")
            try:
                active_jobs = (
                    session.query(Job)
                    .filter(Job.user_id == user_id, Job.status.in_(["queued", "running"]))
                    .count()
                )
                if active_jobs >= settings.max_concurrent_jobs_per_user:
                    raise ValueError("Concurrency limit reached")
                job_record = Job(lab_id=lab_id, user_id=user_id, action=action, status="queued")
                session.add(job_record)
                session.commit()
                session.refresh(job_record)
            finally:
                redis_conn.delete(lock_key)
        else:
            job_record = Job(lab_id=lab_id, user_id=user_id, action=action, status="queued")
            session.add(job_record)
            session.commit()
            session.refresh(job_record)

        queue.enqueue(execute_netlab_action, job_record.id, lab_id, action)
        return job_record
