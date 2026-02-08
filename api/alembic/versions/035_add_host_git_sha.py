"""Add git_sha column to hosts table.

Revision ID: 035
Revises: 034
Create Date: 2026-02-07

Adds:
- hosts.git_sha: Tracks the git commit SHA the agent is running
"""
from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hosts",
        sa.Column("git_sha", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hosts", "git_sha")
