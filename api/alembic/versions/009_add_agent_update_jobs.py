"""Add agent update jobs table and deployment_mode column.

This migration adds:
- agent_update_jobs: Tracks agent software update operations
- deployment_mode column on hosts table for update strategy

Revision ID: 009
Revises: 008
Create Date: 2026-01-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add deployment_mode column to hosts table
    op.add_column(
        'hosts',
        sa.Column('deployment_mode', sa.String(50), nullable=False, server_default='unknown')
    )

    # Create agent_update_jobs table
    op.create_table(
        'agent_update_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('host_id', sa.String(36), sa.ForeignKey('hosts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_version', sa.String(50), nullable=False),
        sa.Column('to_version', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Create indexes for agent_update_jobs
    op.create_index('ix_agent_update_jobs_host_id', 'agent_update_jobs', ['host_id'])
    op.create_index('ix_agent_update_jobs_status', 'agent_update_jobs', ['status'])


def downgrade() -> None:
    # Drop agent_update_jobs table and indexes
    op.drop_index('ix_agent_update_jobs_status', table_name='agent_update_jobs')
    op.drop_index('ix_agent_update_jobs_host_id', table_name='agent_update_jobs')
    op.drop_table('agent_update_jobs')

    # Remove deployment_mode column from hosts
    op.drop_column('hosts', 'deployment_mode')
