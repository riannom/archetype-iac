"""Add last-known Docker snapshotter mode to hosts.

Revision ID: 060
Revises: 059
Create Date: 2026-03-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hosts", sa.Column("docker_snapshotter_mode", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("hosts", "docker_snapshotter_mode")
