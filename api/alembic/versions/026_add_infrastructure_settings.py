"""Add infrastructure settings and agent links tables.

This migration adds:
1. infra_settings - singleton table for global infrastructure configuration
2. agent_links - tracks connectivity between agent pairs for mesh visualization

The infra_settings table stores cluster-wide settings like overlay MTU.
The agent_links table enables visual agent mesh with MTU testing results.

Revision ID: 026
Revises: 025
Create Date: 2026-02-01
"""
from alembic import op
import sqlalchemy as sa

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create infra_settings table
    op.create_table(
        "infra_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("overlay_mtu", sa.Integer(), nullable=False, server_default="1450"),
        sa.Column("mtu_verification_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_by_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )

    # Insert the default singleton row
    op.execute(
        """
        INSERT INTO infra_settings (id, overlay_mtu, mtu_verification_enabled)
        VALUES ('global', 1450, true)
        """
    )

    # Create agent_links table
    op.create_table(
        "agent_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_agent_id",
            sa.String(36),
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_agent_id",
            sa.String(36),
            sa.ForeignKey("hosts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("link_type", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("configured_mtu", sa.Integer(), nullable=False, server_default="1450"),
        sa.Column("tested_mtu", sa.Integer(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_status", sa.String(20), nullable=False, server_default="untested"),
        sa.Column("test_error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
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
        sa.UniqueConstraint("source_agent_id", "target_agent_id", name="uq_agent_link_pair"),
    )


def downgrade() -> None:
    op.drop_table("agent_links")
    op.drop_table("infra_settings")
