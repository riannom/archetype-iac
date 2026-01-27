"""Add NodeState model for per-node lifecycle management.

Revision ID: 004
Revises: 003
Create Date: 2026-01-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create node_states table
    op.create_table(
        'node_states',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lab_id', sa.String(36), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_id', sa.String(100), nullable=False),
        sa.Column('node_name', sa.String(100), nullable=False),
        sa.Column('desired_state', sa.String(50), nullable=False, server_default='stopped'),
        sa.Column('actual_state', sa.String(50), nullable=False, server_default='undeployed'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('lab_id', 'node_id', name='uq_node_state_lab_node'),
    )

    # Create index on lab_id for fast lookups
    op.create_index('ix_node_states_lab_id', 'node_states', ['lab_id'])


def downgrade() -> None:
    op.drop_index('ix_node_states_lab_id', table_name='node_states')
    op.drop_table('node_states')
