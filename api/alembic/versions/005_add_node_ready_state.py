"""Add boot readiness tracking to NodeState.

Revision ID: 005
Revises: 004
Create Date: 2026-01-27

Adds is_ready and boot_started_at columns to track when nodes have
completed their boot sequence and are ready for console interaction.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_ready column - defaults to False until boot probe confirms ready
    op.add_column(
        'node_states',
        sa.Column('is_ready', sa.Boolean(), server_default='false', nullable=False)
    )
    # Add boot_started_at for tracking boot duration
    op.add_column(
        'node_states',
        sa.Column('boot_started_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('node_states', 'boot_started_at')
    op.drop_column('node_states', 'is_ready')
