"""Add stopping_started_at to node_states.

This migration adds the stopping_started_at column to track when a node
entered the "stopping" transitional state. This enables:
1. Timeout recovery for stuck stopping operations
2. Visual feedback in the UI while nodes are stopping

Revision ID: 021
Revises: 020
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "node_states",
        sa.Column("stopping_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_states", "stopping_started_at")
