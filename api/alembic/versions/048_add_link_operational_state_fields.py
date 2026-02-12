"""Add per-endpoint operational state fields to link_states.

Revision ID: 048
Revises: 047
Create Date: 2026-02-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "link_states",
        sa.Column("source_oper_state", sa.String(length=20), nullable=False, server_default="down"),
    )
    op.add_column(
        "link_states",
        sa.Column("target_oper_state", sa.String(length=20), nullable=False, server_default="down"),
    )
    op.add_column(
        "link_states",
        sa.Column("source_oper_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("target_oper_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("source_last_change_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("target_last_change_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "link_states",
        sa.Column("oper_epoch", sa.Integer(), nullable=False, server_default="0"),
    )

    op.execute(
        """
        UPDATE link_states
        SET
            source_oper_state = CASE WHEN source_carrier_state = 'on' THEN 'up' ELSE 'down' END,
            target_oper_state = CASE WHEN target_carrier_state = 'on' THEN 'up' ELSE 'down' END,
            oper_epoch = 0
        """
    )

    op.alter_column("link_states", "source_oper_state", server_default=None)
    op.alter_column("link_states", "target_oper_state", server_default=None)
    op.alter_column("link_states", "oper_epoch", server_default=None)


def downgrade() -> None:
    op.drop_column("link_states", "oper_epoch")
    op.drop_column("link_states", "target_last_change_at")
    op.drop_column("link_states", "source_last_change_at")
    op.drop_column("link_states", "target_oper_reason")
    op.drop_column("link_states", "source_oper_reason")
    op.drop_column("link_states", "target_oper_state")
    op.drop_column("link_states", "source_oper_state")
