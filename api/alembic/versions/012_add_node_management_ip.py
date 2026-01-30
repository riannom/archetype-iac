"""Add management IP tracking to NodeState.

Revision ID: 012
Revises: 011
Create Date: 2026-01-30

Adds management_ip and management_ips_json columns to track
node IP addresses for IaC workflow integration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add management_ip column - primary management IP address
    op.add_column(
        'node_states',
        sa.Column('management_ip', sa.String(255), nullable=True)
    )
    # Add management_ips_json for all IPs (JSON array)
    op.add_column(
        'node_states',
        sa.Column('management_ips_json', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('node_states', 'management_ips_json')
    op.drop_column('node_states', 'management_ip')
