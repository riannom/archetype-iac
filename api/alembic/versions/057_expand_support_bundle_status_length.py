"""Expand support bundle status column length.

Revision ID: 057
Revises: 056
Create Date: 2026-02-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "support_bundles",
        "status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "support_bundles",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
