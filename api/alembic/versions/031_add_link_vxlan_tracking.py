"""Add VXLAN attachment tracking columns to LinkState.

These columns track which side of a cross-host link has been successfully
attached to the overlay network. This enables partial recovery when an
agent restarts - only the affected side needs to be re-attached rather
than rebuilding the entire link.

Revision ID: 031
Revises: 030
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add VXLAN attachment tracking columns
    op.add_column(
        "link_states",
        sa.Column("source_vxlan_attached", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "link_states",
        sa.Column("target_vxlan_attached", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Set existing cross-host links that are "up" to have both sides attached
    # This assumes current "up" links have both sides properly configured
    op.execute("""
        UPDATE link_states
        SET source_vxlan_attached = true, target_vxlan_attached = true
        WHERE is_cross_host = true AND actual_state = 'up'
    """)


def downgrade() -> None:
    op.drop_column("link_states", "target_vxlan_attached")
    op.drop_column("link_states", "source_vxlan_attached")
