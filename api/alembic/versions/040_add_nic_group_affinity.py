"""Add NIC groups and external network affinities.

Revision ID: 040
Revises: 039
Create Date: 2026-02-10

Adds:
- host_nic_groups
- host_nic_group_members
- external_network_affinities
"""
from alembic import op
import sqlalchemy as sa

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_nic_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(36), sa.ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("host_id", "name", name="uq_host_nic_group_name"),
    )
    op.create_index("ix_host_nic_groups_host_id", "host_nic_groups", ["host_id"])

    op.create_table(
        "host_nic_group_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("nic_group_id", sa.String(36), sa.ForeignKey("host_nic_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("managed_interface_id", sa.String(36), sa.ForeignKey("agent_managed_interfaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("nic_group_id", "managed_interface_id", name="uq_nic_group_member"),
    )
    op.create_index("ix_host_nic_group_members_nic_group_id", "host_nic_group_members", ["nic_group_id"])
    op.create_index(
        "ix_host_nic_group_members_managed_interface_id",
        "host_nic_group_members",
        ["managed_interface_id"],
    )

    op.create_table(
        "external_network_affinities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_node_id", sa.String(36), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "preferred_nic_group_id",
            sa.String(36),
            sa.ForeignKey("host_nic_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "preferred_managed_interface_id",
            sa.String(36),
            sa.ForeignKey("agent_managed_interfaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("lab_id", "external_node_id", name="uq_external_affinity"),
    )
    op.create_index("ix_external_network_affinities_lab_id", "external_network_affinities", ["lab_id"])
    op.create_index(
        "ix_external_network_affinities_external_node_id",
        "external_network_affinities",
        ["external_node_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_network_affinities_external_node_id", table_name="external_network_affinities")
    op.drop_index("ix_external_network_affinities_lab_id", table_name="external_network_affinities")
    op.drop_table("external_network_affinities")

    op.drop_index("ix_host_nic_group_members_managed_interface_id", table_name="host_nic_group_members")
    op.drop_index("ix_host_nic_group_members_nic_group_id", table_name="host_nic_group_members")
    op.drop_table("host_nic_group_members")

    op.drop_index("ix_host_nic_groups_host_id", table_name="host_nic_groups")
    op.drop_table("host_nic_groups")
