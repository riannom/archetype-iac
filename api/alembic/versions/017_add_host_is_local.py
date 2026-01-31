"""Add is_local column to hosts table.

This migration adds:
- is_local: Whether the agent is co-located with the controller (enables rebuild)

Revision ID: 017
Revises: 016
Create Date: 2026-01-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '017'
down_revision: Union[str, None] = '016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_local column to hosts table
    op.add_column(
        'hosts',
        sa.Column('is_local', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    # Remove is_local column from hosts
    op.drop_column('hosts', 'is_local')
