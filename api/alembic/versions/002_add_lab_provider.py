"""Add provider column to Lab model.

Revision ID: 002
Revises: 001
Create Date: 2026-01-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add provider column to labs table with default value
    op.add_column(
        'labs',
        sa.Column('provider', sa.String(50), nullable=False, server_default='containerlab')
    )


def downgrade() -> None:
    op.drop_column('labs', 'provider')
