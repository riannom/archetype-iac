"""Add image sync status tracking to NodeState.

Revision ID: 010
Revises: 009
Create Date: 2026-01-28

Adds image_sync_status and image_sync_message columns to track
image push progress to agents during deployment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '010'
down_revision: Union[str, None] = '009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add image_sync_status column - null when not syncing
    # Values: null, "checking", "syncing", "synced", "failed"
    op.add_column(
        'node_states',
        sa.Column('image_sync_status', sa.String(50), nullable=True)
    )
    # Add image_sync_message for progress/error details
    op.add_column(
        'node_states',
        sa.Column('image_sync_message', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('node_states', 'image_sync_message')
    op.drop_column('node_states', 'image_sync_status')
