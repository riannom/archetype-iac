"""Add starting_started_at to node_states.

This migration adds the starting_started_at column to track when a node
entered the "starting" transitional state. This enables:
1. Timeout recovery for stuck starting operations
2. Visual feedback in the UI while nodes are starting
3. Completion logging for start operations

Revision ID: 023
Revises: 022
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "node_states",
        sa.Column("starting_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node_states", "starting_started_at")
