"""Add image synchronization tables for multi-agent deployments.

This migration adds:
- image_hosts: Tracks which images exist on which agents
- image_sync_jobs: Tracks transfer operations with progress
- image_sync_strategy column on hosts table

Revision ID: 008
Revises: 007
Create Date: 2026-01-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add image_sync_strategy column to hosts table
    op.add_column(
        'hosts',
        sa.Column('image_sync_strategy', sa.String(50), nullable=False, server_default='on_demand')
    )

    # Create image_hosts table
    op.create_table(
        'image_hosts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('image_id', sa.String(255), nullable=False),
        sa.Column('host_id', sa.String(36), sa.ForeignKey('hosts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reference', sa.String(255), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='unknown'),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Create indexes for image_hosts
    op.create_index('ix_image_hosts_image_id', 'image_hosts', ['image_id'])
    op.create_index('ix_image_hosts_host_id', 'image_hosts', ['host_id'])
    op.create_index('ix_image_hosts_status', 'image_hosts', ['status'])
    op.create_unique_constraint('uq_image_host', 'image_hosts', ['image_id', 'host_id'])

    # Create image_sync_jobs table
    op.create_table(
        'image_sync_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('image_id', sa.String(255), nullable=False),
        sa.Column('host_id', sa.String(36), sa.ForeignKey('hosts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bytes_transferred', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('total_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Create indexes for image_sync_jobs
    op.create_index('ix_image_sync_jobs_image_id', 'image_sync_jobs', ['image_id'])
    op.create_index('ix_image_sync_jobs_host_id', 'image_sync_jobs', ['host_id'])
    op.create_index('ix_image_sync_jobs_status', 'image_sync_jobs', ['status'])


def downgrade() -> None:
    # Drop image_sync_jobs table and indexes
    op.drop_index('ix_image_sync_jobs_status', table_name='image_sync_jobs')
    op.drop_index('ix_image_sync_jobs_host_id', table_name='image_sync_jobs')
    op.drop_index('ix_image_sync_jobs_image_id', table_name='image_sync_jobs')
    op.drop_table('image_sync_jobs')

    # Drop image_hosts table and indexes
    op.drop_constraint('uq_image_host', 'image_hosts', type_='unique')
    op.drop_index('ix_image_hosts_status', table_name='image_hosts')
    op.drop_index('ix_image_hosts_host_id', table_name='image_hosts')
    op.drop_index('ix_image_hosts_image_id', table_name='image_hosts')
    op.drop_table('image_hosts')

    # Remove image_sync_strategy column from hosts
    op.drop_column('hosts', 'image_sync_strategy')
