"""Data plane separation: transport config and managed interfaces.

Revision ID: 036
Revises: 035
Create Date: 2026-02-08

Adds:
- hosts.data_plane_address: Separate IP for VXLAN tunnels (vs management address)
- agent_network_configs: Transport mode fields (subinterface, dedicated, management)
- agent_managed_interfaces: New table for tracking provisioned interfaces
"""
from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add data_plane_address to hosts
    op.add_column(
        "hosts",
        sa.Column("data_plane_address", sa.String(255), nullable=True),
    )

    # 2. Add transport fields to agent_network_configs
    op.add_column(
        "agent_network_configs",
        sa.Column("transport_mode", sa.String(20), server_default="management", nullable=False),
    )
    op.add_column(
        "agent_network_configs",
        sa.Column("parent_interface", sa.String(64), nullable=True),
    )
    op.add_column(
        "agent_network_configs",
        sa.Column("vlan_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_network_configs",
        sa.Column("transport_ip", sa.String(255), nullable=True),
    )
    op.add_column(
        "agent_network_configs",
        sa.Column("transport_subnet", sa.String(255), nullable=True),
    )

    # 3. Create agent_managed_interfaces table
    op.create_table(
        "agent_managed_interfaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("interface_type", sa.String(20), nullable=False),  # transport, external, custom
        sa.Column("parent_interface", sa.String(64), nullable=True),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(255), nullable=True),
        sa.Column("desired_mtu", sa.Integer(), server_default="9000", nullable=False),
        sa.Column("current_mtu", sa.Integer(), nullable=True),
        sa.Column("is_up", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("sync_status", sa.String(20), server_default="unconfigured", nullable=False),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("host_id", "name", name="uq_managed_interface_host_name"),
    )
    op.create_index("ix_agent_managed_interfaces_host_id", "agent_managed_interfaces", ["host_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_managed_interfaces_host_id", table_name="agent_managed_interfaces")
    op.drop_table("agent_managed_interfaces")
    op.drop_column("agent_network_configs", "transport_subnet")
    op.drop_column("agent_network_configs", "transport_ip")
    op.drop_column("agent_network_configs", "vlan_id")
    op.drop_column("agent_network_configs", "parent_interface")
    op.drop_column("agent_network_configs", "transport_mode")
    op.drop_column("hosts", "data_plane_address")
