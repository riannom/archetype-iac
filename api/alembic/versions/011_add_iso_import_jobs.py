"""Add ISO import jobs table.

This migration adds:
- iso_import_jobs: Tracks ISO image import operations

Revision ID: 011
Revises: 010
Create Date: 2026-01-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create iso_import_jobs table
    op.create_table(
        'iso_import_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('iso_path', sa.String(500), nullable=False),
        sa.Column('format', sa.String(50), nullable=False, server_default='unknown'),
        sa.Column('manifest_json', sa.Text(), nullable=True),
        sa.Column('selected_images', sa.Text(), nullable=True),
        sa.Column('image_progress', sa.Text(), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('images_imported', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('images_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Create indexes
    op.create_index('ix_iso_import_jobs_user_id', 'iso_import_jobs', ['user_id'])
    op.create_index('ix_iso_import_jobs_status', 'iso_import_jobs', ['status'])


def downgrade() -> None:
    # Drop indexes and table
    op.drop_index('ix_iso_import_jobs_status', table_name='iso_import_jobs')
    op.drop_index('ix_iso_import_jobs_user_id', table_name='iso_import_jobs')
    op.drop_table('iso_import_jobs')
