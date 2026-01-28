"""Add ConfigSnapshot model for config versioning and diff support.

Revision ID: 007
Revises: 006
Create Date: 2026-01-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create config_snapshots table
    op.create_table(
        'config_snapshots',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lab_id', sa.String(36), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('node_name', sa.String(100), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('snapshot_type', sa.String(50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Create indexes for common query patterns
    op.create_index('ix_config_snapshots_lab_id', 'config_snapshots', ['lab_id'])
    op.create_index('ix_config_snapshots_lab_node', 'config_snapshots', ['lab_id', 'node_name'])
    op.create_index('ix_config_snapshots_content_hash', 'config_snapshots', ['content_hash'])


def downgrade() -> None:
    op.drop_index('ix_config_snapshots_content_hash', table_name='config_snapshots')
    op.drop_index('ix_config_snapshots_lab_node', table_name='config_snapshots')
    op.drop_index('ix_config_snapshots_lab_id', table_name='config_snapshots')
    op.drop_table('config_snapshots')
