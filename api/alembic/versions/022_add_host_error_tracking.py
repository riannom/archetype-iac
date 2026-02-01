"""Add error tracking to hosts table.

This migration adds columns to track agent-level errors that persist
across operations (e.g., Docker state corruption, unreachable agents).

Fields added:
- last_error: Text error message from the most recent failure
- error_since: Timestamp when the error condition first occurred

These fields are populated by the reconciliation task and cleared when
the agent successfully responds.

Revision ID: 022
Revises: 021
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosts",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "hosts",
        sa.Column("error_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosts", "error_since")
    op.drop_column("hosts", "last_error")
