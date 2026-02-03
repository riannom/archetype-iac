"""Add agent network configuration table.

This migration adds the agent_network_configs table for tracking per-agent
physical interface MTU configuration. This enables administrators to configure
and monitor the data plane interface MTU on each agent host.

Revision ID: 029
Revises: 028
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_network_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "host_id",
            sa.String(36),
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        # The physical interface to configure (e.g., "eth0", "ens192")
        sa.Column("data_plane_interface", sa.String(64), nullable=True),
        # User-desired MTU (default 9000 for jumbo frames)
        sa.Column("desired_mtu", sa.Integer(), nullable=False, server_default="9000"),
        # Last known actual MTU on the interface
        sa.Column("current_mtu", sa.Integer(), nullable=True),
        # When we last synced/checked the MTU
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        # Sync status: synced, mismatch, error, unknown, unconfigured
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="unconfigured"),
        # Error message if sync failed
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_network_configs")
