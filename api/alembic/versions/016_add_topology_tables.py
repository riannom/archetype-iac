"""Add Node and Link topology tables.

This migration creates tables to store topology definitions (nodes, links)
in the database, making it the authoritative source for topology structure.
YAML becomes an import/export format only.

Revision ID: 016
Revises: 015
Create Date: 2026-01-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create nodes table - topology node definitions
    op.create_table(
        'nodes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lab_id', sa.String(36), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False),
        # Identity
        sa.Column('gui_id', sa.String(100), nullable=False),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('container_name', sa.String(100), nullable=False),
        # Device config
        sa.Column('node_type', sa.String(50), nullable=False, server_default='device'),
        sa.Column('device', sa.String(100), nullable=True),
        sa.Column('image', sa.String(255), nullable=True),
        sa.Column('version', sa.String(50), nullable=True),
        sa.Column('network_mode', sa.String(50), nullable=True),
        # Placement
        sa.Column('host_id', sa.String(36), sa.ForeignKey('hosts.id'), nullable=True),
        # External network fields
        sa.Column('connection_type', sa.String(50), nullable=True),
        sa.Column('parent_interface', sa.String(100), nullable=True),
        sa.Column('vlan_id', sa.Integer(), nullable=True),
        sa.Column('bridge_name', sa.String(100), nullable=True),
        # Extra config as JSON
        sa.Column('config_json', sa.Text(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Unique constraint: one container_name per lab
        sa.UniqueConstraint('lab_id', 'container_name', name='uq_node_lab_container'),
    )

    # Create indexes on nodes table
    op.create_index('ix_nodes_lab_id', 'nodes', ['lab_id'])
    op.create_index('ix_nodes_gui_id', 'nodes', ['lab_id', 'gui_id'])

    # Create links table - topology link definitions
    op.create_table(
        'links',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('lab_id', sa.String(36), sa.ForeignKey('labs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('link_name', sa.String(255), nullable=False),
        # Source endpoint
        sa.Column('source_node_id', sa.String(36), sa.ForeignKey('nodes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_interface', sa.String(100), nullable=False),
        # Target endpoint
        sa.Column('target_node_id', sa.String(36), sa.ForeignKey('nodes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('target_interface', sa.String(100), nullable=False),
        # Link properties
        sa.Column('mtu', sa.Integer(), nullable=True),
        sa.Column('bandwidth', sa.Integer(), nullable=True),
        # Extra config as JSON
        sa.Column('config_json', sa.Text(), nullable=True),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Unique constraint: one link_name per lab
        sa.UniqueConstraint('lab_id', 'link_name', name='uq_link_lab_name'),
    )

    # Create indexes on links table
    op.create_index('ix_links_lab_id', 'links', ['lab_id'])

    # Add node_definition_id FK column to node_states table
    op.add_column(
        'node_states',
        sa.Column('node_definition_id', sa.String(36), nullable=True)
    )
    op.create_foreign_key(
        'fk_node_states_node_definition',
        'node_states',
        'nodes',
        ['node_definition_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add link_definition_id FK column to link_states table
    op.add_column(
        'link_states',
        sa.Column('link_definition_id', sa.String(36), nullable=True)
    )
    op.create_foreign_key(
        'fk_link_states_link_definition',
        'link_states',
        'links',
        ['link_definition_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove FK columns from state tables
    op.drop_constraint('fk_link_states_link_definition', 'link_states', type_='foreignkey')
    op.drop_column('link_states', 'link_definition_id')

    op.drop_constraint('fk_node_states_node_definition', 'node_states', type_='foreignkey')
    op.drop_column('node_states', 'node_definition_id')

    # Drop links table and its indexes
    op.drop_index('ix_links_lab_id', table_name='links')
    op.drop_table('links')

    # Drop nodes table and its indexes
    op.drop_index('ix_nodes_gui_id', table_name='nodes')
    op.drop_index('ix_nodes_lab_id', table_name='nodes')
    op.drop_table('nodes')
