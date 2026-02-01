"""Add parent_job_id and superseded_by_id to jobs table.

This migration adds tracking for parent-child job relationships and
job supersession to prevent job multiplication during retries.

Fields:
- parent_job_id: Links child jobs (e.g., sync:agent) to their parent (sync:lab)
- superseded_by_id: Tracks which job replaced this one when cancelled/retried

This enables:
1. Skipping child job retries when parent is still active
2. Cancelling children when parent is retried (new children will be spawned)
3. Deduplication of retry jobs

Revision ID: 024
Revises: 023
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add parent_job_id column
    op.add_column(
        "jobs",
        sa.Column(
            "parent_job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Add superseded_by_id column
    op.add_column(
        "jobs",
        sa.Column(
            "superseded_by_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Create index for efficient child job lookups
    op.create_index("ix_jobs_parent_job_id", "jobs", ["parent_job_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_parent_job_id", table_name="jobs")
    op.drop_column("jobs", "superseded_by_id")
    op.drop_column("jobs", "parent_job_id")
