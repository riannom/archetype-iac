"""Add per-side VLAN tag columns to link_states.

Stores the local OVS VLAN tag for each endpoint of a link, enabling
detection and repair of VLAN drift after container restarts.

Revision ID: 049
Revises: 048
Create Date: 2026-02-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "link_states",
        sa.Column("source_vlan_tag", sa.Integer(), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("target_vlan_tag", sa.Integer(), nullable=True),
    )

    # Backfill same-host links: both sides share the existing vlan_tag
    op.execute(
        """
        UPDATE link_states
        SET source_vlan_tag = vlan_tag, target_vlan_tag = vlan_tag
        WHERE vlan_tag IS NOT NULL AND is_cross_host = false
        """
    )


def downgrade() -> None:
    op.drop_column("link_states", "target_vlan_tag")
    op.drop_column("link_states", "source_vlan_tag")
