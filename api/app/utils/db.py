"""Database transaction management utilities.

Shared helpers for releasing DB transactions before long external I/O awaits.
Controller/task code must not hold SQLAlchemy transactions across awaited
agent RPCs or network calls — doing so risks idle-in-transaction timeouts.
"""
from __future__ import annotations

import logging
from time import perf_counter

from app.metrics import (
    record_db_transaction_issue,
    record_db_transaction_release_duration,
)

logger = logging.getLogger(__name__)


def reset_session_after_db_error(
    session,
    *,
    context: str,
    table: str = "unknown",
    lab_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Best-effort rollback to recover a failed SQLAlchemy session."""
    try:
        session.rollback()
    except Exception as rollback_error:
        record_db_transaction_issue(
            issue="rollback_failed",
            phase=context,
            table=table,
        )
        logger.warning(
            "Failed to rollback DB session after %s: %s",
            context,
            rollback_error,
            extra={
                "event": "db_transaction_issue",
                "issue": "rollback_failed",
                "phase": context,
                "table": table,
                "lab_id": lab_id,
                "job_id": job_id,
            },
        )


def release_db_transaction_for_io(
    session,
    *,
    context: str,
    table: str = "unknown",
    lab_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Close open transaction boundaries before long external awaits."""
    has_pending_writes = bool(session.new or session.dirty or session.deleted)
    started = perf_counter()
    try:
        if has_pending_writes:
            session.commit()
        else:
            session.rollback()
        record_db_transaction_release_duration(
            duration_seconds=perf_counter() - started,
            phase=context,
            table=table,
            result="success",
        )
    except Exception as exc:
        issue = "statement_timeout" if "statement timeout" in str(exc).lower() else "release_failed"
        record_db_transaction_release_duration(
            duration_seconds=perf_counter() - started,
            phase=context,
            table=table,
            result=issue,
        )
        record_db_transaction_issue(
            issue=issue,
            phase=context,
            table=table,
        )
        logger.warning(
            "Failed to release DB transaction before %s: %s",
            context,
            exc,
            extra={
                "event": "db_transaction_issue",
                "issue": issue,
                "phase": context,
                "table": table,
                "lab_id": lab_id,
                "job_id": job_id,
            },
        )
        reset_session_after_db_error(
            session,
            context=context,
            table=table,
            lab_id=lab_id,
            job_id=job_id,
        )
        raise
