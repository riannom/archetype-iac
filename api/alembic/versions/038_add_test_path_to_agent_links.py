"""Add test_path to agent_links table.

Revision ID: 038
Revises: 037
Create Date: 2026-02-08

Adds:
- agent_links.test_path: which network path was tested ("data_plane" or "management")
"""
from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_links",
        sa.Column("test_path", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_links", "test_path")
