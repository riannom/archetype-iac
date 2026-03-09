"""Add catalog-backed Docker image archive metadata.

Revision ID: 061
Revises: 060
Create Date: 2026-03-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog_images", sa.Column("archive_path", sa.String(length=500), nullable=True))
    op.add_column(
        "catalog_images",
        sa.Column("archive_status", sa.String(length=20), nullable=False, server_default="none"),
    )
    op.add_column("catalog_images", sa.Column("archive_sha256", sa.String(length=128), nullable=True))
    op.add_column("catalog_images", sa.Column("archive_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("catalog_images", sa.Column("archive_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("catalog_images", sa.Column("archive_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("catalog_images", sa.Column("archive_error", sa.Text(), nullable=True))
    op.alter_column("catalog_images", "archive_status", server_default=None)


def downgrade() -> None:
    op.drop_column("catalog_images", "archive_error")
    op.drop_column("catalog_images", "archive_verified_at")
    op.drop_column("catalog_images", "archive_created_at")
    op.drop_column("catalog_images", "archive_size_bytes")
    op.drop_column("catalog_images", "archive_sha256")
    op.drop_column("catalog_images", "archive_status")
    op.drop_column("catalog_images", "archive_path")
