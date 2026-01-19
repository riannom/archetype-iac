"""Add state tracking fields to Lab and Job models.

Revision ID: 001
Revises:
Create Date: 2026-01-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to labs table
    op.add_column('labs', sa.Column('state', sa.String(50), nullable=False, server_default='stopped'))
    op.add_column('labs', sa.Column('agent_id', sa.String(36), sa.ForeignKey('hosts.id'), nullable=True))
    op.add_column('labs', sa.Column('state_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('labs', sa.Column('state_error', sa.Text(), nullable=True))

    # Add new columns to jobs table
    op.add_column('jobs', sa.Column('agent_id', sa.String(36), sa.ForeignKey('hosts.id'), nullable=True))
    op.add_column('jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    # Remove columns from jobs table
    op.drop_column('jobs', 'retry_count')
    op.drop_column('jobs', 'completed_at')
    op.drop_column('jobs', 'started_at')
    op.drop_column('jobs', 'agent_id')

    # Remove columns from labs table
    op.drop_column('labs', 'state_error')
    op.drop_column('labs', 'state_updated_at')
    op.drop_column('labs', 'agent_id')
    op.drop_column('labs', 'state')
