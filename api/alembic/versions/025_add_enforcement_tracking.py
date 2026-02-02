"""Add enforcement tracking columns to node_states.

This migration adds columns to track state enforcement attempts:
- enforcement_attempts: Count of enforcement attempts on this node
- last_enforcement_at: When the last enforcement was attempted
- enforcement_failed_at: When enforcement was marked as failed (max retries)

This enables:
1. Tracking retry attempts with exponential backoff
2. Stopping enforcement after max retries
3. Crash cooldown before auto-restart
4. UI indicators for nodes exceeding max retries

Revision ID: 025
Revises: 024
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add enforcement_attempts column with default 0
    op.add_column(
        "node_states",
        sa.Column(
            "enforcement_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Add last_enforcement_at column
    op.add_column(
        "node_states",
        sa.Column(
            "last_enforcement_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Add enforcement_failed_at column
    op.add_column(
        "node_states",
        sa.Column(
            "enforcement_failed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("node_states", "enforcement_failed_at")
    op.drop_column("node_states", "last_enforcement_at")
    op.drop_column("node_states", "enforcement_attempts")
