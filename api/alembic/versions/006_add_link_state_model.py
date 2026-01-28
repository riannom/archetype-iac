"""Add LinkState model for per-link operational state tracking.

Revision ID: 006
Revises: 005
Create Date: 2026-01-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create link_states table
    op.create_table(
        'link_states',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lab_id', sa.String(36), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('link_name', sa.String(255), nullable=False),
        sa.Column('source_node', sa.String(100), nullable=False),
        sa.Column('source_interface', sa.String(100), nullable=False),
        sa.Column('target_node', sa.String(100), nullable=False),
        sa.Column('target_interface', sa.String(100), nullable=False),
        sa.Column('desired_state', sa.String(50), nullable=False, server_default='up'),
        sa.Column('actual_state', sa.String(50), nullable=False, server_default='unknown'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('lab_id', 'link_name', name='uq_link_state_lab_link'),
    )

    # Create index on lab_id for fast lookups
    op.create_index('ix_link_states_lab_id', 'link_states', ['lab_id'])


def downgrade() -> None:
    op.drop_index('ix_link_states_lab_id', table_name='link_states')
    op.drop_table('link_states')
