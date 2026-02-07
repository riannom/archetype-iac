"""Add config management fields for mapping, active config, and device kind.

Revision ID: 034
Revises: 033
Create Date: 2026-02-07

Adds:
- config_snapshots.device_kind: Device type for type-matching during config mapping
- config_snapshots.mapped_to_node_id: FK to nodes for reassigning orphaned configs
- nodes.active_config_snapshot_id: FK to config_snapshots for active startup-config
"""
from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add device_kind to config_snapshots for type-matching
    op.add_column(
        "config_snapshots",
        sa.Column("device_kind", sa.String(100), nullable=True),
    )
    op.create_index("ix_config_snapshots_device_kind", "config_snapshots", ["device_kind"])

    # Add mapped_to_node_id for config reassignment
    op.add_column(
        "config_snapshots",
        sa.Column(
            "mapped_to_node_id",
            sa.String(36),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_config_snapshots_mapped_to_node_id", "config_snapshots", ["mapped_to_node_id"])

    # Add active_config_snapshot_id to nodes
    op.add_column(
        "nodes",
        sa.Column(
            "active_config_snapshot_id",
            sa.String(36),
            sa.ForeignKey("config_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("nodes", "active_config_snapshot_id")
    op.drop_index("ix_config_snapshots_mapped_to_node_id", table_name="config_snapshots")
    op.drop_column("config_snapshots", "mapped_to_node_id")
    op.drop_index("ix_config_snapshots_device_kind", table_name="config_snapshots")
    op.drop_column("config_snapshots", "device_kind")
