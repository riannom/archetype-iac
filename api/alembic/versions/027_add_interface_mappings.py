"""Add interface mappings table.

This migration adds the interface_mappings table for tracking the mapping
between OVS ports, Linux interfaces, and vendor-specific interface names.

This enables:
- Translating between eth1 and Ethernet1 (vendor names)
- Looking up OVS port names for verification
- Querying VLAN tags for link validation

Revision ID: 027
Revises: 026
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interface_mappings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lab_id", sa.String(36), sa.ForeignKey("labs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(36), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
        # OVS layer
        sa.Column("ovs_port", sa.String(20), nullable=True),  # e.g., "vh614ed63ed40"
        sa.Column("ovs_bridge", sa.String(50), nullable=True),  # e.g., "arch-ovs"
        sa.Column("vlan_tag", sa.Integer(), nullable=True),
        # Linux layer
        sa.Column("linux_interface", sa.String(20), nullable=False),  # e.g., "eth1"
        # Vendor layer
        sa.Column("vendor_interface", sa.String(50), nullable=True),  # e.g., "Ethernet1"
        sa.Column("device_type", sa.String(50), nullable=True),  # e.g., "arista_ceos"
        # Metadata
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        # Unique constraint: one mapping per interface per node per lab
        sa.UniqueConstraint("lab_id", "node_id", "linux_interface", name="uq_interface_mapping_lab_node_interface"),
    )
    # Index for common lookups
    op.create_index("ix_interface_mappings_lab_id", "interface_mappings", ["lab_id"])
    op.create_index("ix_interface_mappings_node_id", "interface_mappings", ["node_id"])
    op.create_index("ix_interface_mappings_ovs_port", "interface_mappings", ["ovs_port"])


def downgrade() -> None:
    op.drop_index("ix_interface_mappings_ovs_port", "interface_mappings")
    op.drop_index("ix_interface_mappings_node_id", "interface_mappings")
    op.drop_index("ix_interface_mappings_lab_id", "interface_mappings")
    op.drop_table("interface_mappings")
