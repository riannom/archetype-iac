"""Add started_at column to hosts table.

This migration adds:
- started_at: When the agent process started (for uptime tracking)

Revision ID: 015
Revises: 014
Create Date: 2026-01-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '015'
down_revision: Union[str, None] = '014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add started_at column to hosts table for uptime tracking
    op.add_column(
        'hosts',
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    # Remove started_at column from hosts
    op.drop_column('hosts', 'started_at')
