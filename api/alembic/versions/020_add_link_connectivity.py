"""Add link connectivity and VXLAN tunnel support.

This migration adds:
1. Cross-host VXLAN fields to link_states table
2. Carrier state tracking per endpoint
3. New vxlan_tunnels table for tracking cross-host tunnels

Revision ID: 020
Revises: 019
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cross-host VXLAN support columns to link_states
    op.add_column(
        "link_states",
        sa.Column("is_cross_host", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "link_states",
        sa.Column("vni", sa.Integer(), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("vlan_tag", sa.Integer(), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("source_host_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("target_host_id", sa.String(36), nullable=True),
    )

    # Add carrier state tracking columns
    op.add_column(
        "link_states",
        sa.Column("source_carrier_state", sa.String(10), nullable=False, server_default="on"),
    )
    op.add_column(
        "link_states",
        sa.Column("target_carrier_state", sa.String(10), nullable=False, server_default="on"),
    )

    # Add foreign keys for host references
    op.create_foreign_key(
        "fk_link_states_source_host",
        "link_states", "hosts",
        ["source_host_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_link_states_target_host",
        "link_states", "hosts",
        ["target_host_id"], ["id"],
    )

    # Create vxlan_tunnels table
    op.create_table(
        "vxlan_tunnels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("link_state_id", sa.String(36), sa.ForeignKey("link_states.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vni", sa.Integer(), nullable=False, index=True),
        sa.Column("vlan_tag", sa.Integer(), nullable=False),
        sa.Column("agent_a_id", sa.String(36), sa.ForeignKey("hosts.id"), nullable=False, index=True),
        sa.Column("agent_a_ip", sa.String(45), nullable=False),
        sa.Column("agent_b_id", sa.String(36), sa.ForeignKey("hosts.id"), nullable=False, index=True),
        sa.Column("agent_b_ip", sa.String(45), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("lab_id", "vni", name="uq_vxlan_tunnel_lab_vni"),
    )


def downgrade() -> None:
    # Drop vxlan_tunnels table
    op.drop_table("vxlan_tunnels")

    # Drop foreign keys from link_states
    op.drop_constraint("fk_link_states_target_host", "link_states", type_="foreignkey")
    op.drop_constraint("fk_link_states_source_host", "link_states", type_="foreignkey")

    # Drop new columns from link_states
    op.drop_column("link_states", "target_carrier_state")
    op.drop_column("link_states", "source_carrier_state")
    op.drop_column("link_states", "target_host_id")
    op.drop_column("link_states", "source_host_id")
    op.drop_column("link_states", "vlan_tag")
    op.drop_column("link_states", "vni")
    op.drop_column("link_states", "is_cross_host")
