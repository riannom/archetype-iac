"""Add last_heartbeat to jobs table.

Revision ID: 018
Revises: 017
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "last_heartbeat")
