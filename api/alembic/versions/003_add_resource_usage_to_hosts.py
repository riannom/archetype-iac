"""Add resource_usage column to Host model.

Revision ID: 003
Revises: 002
Create Date: 2026-01-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add resource_usage column to hosts table with default empty JSON
    op.add_column(
        'hosts',
        sa.Column('resource_usage', sa.Text(), nullable=False, server_default='{}')
    )


def downgrade() -> None:
    op.drop_column('hosts', 'resource_usage')
