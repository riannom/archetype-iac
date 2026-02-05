"""Add overlay MTU preservation flags to infra settings.

Revision ID: 033
Revises: 032
Create Date: 2026-02-05
"""
from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "infra_settings",
        sa.Column("overlay_preserve_container_mtu", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "infra_settings",
        sa.Column("overlay_clamp_host_mtu", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("infra_settings", "overlay_preserve_container_mtu", server_default=None)
    op.alter_column("infra_settings", "overlay_clamp_host_mtu", server_default=None)


def downgrade() -> None:
    op.drop_column("infra_settings", "overlay_clamp_host_mtu")
    op.drop_column("infra_settings", "overlay_preserve_container_mtu")
