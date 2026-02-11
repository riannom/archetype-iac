"""Add theme settings to user preferences.

Revision ID: 045
Revises: 044
Create Date: 2026-02-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("theme_settings", sa.Text(), nullable=False, server_default="{}"),
    )
    op.alter_column("user_preferences", "theme_settings", server_default=None)


def downgrade() -> None:
    op.drop_column("user_preferences", "theme_settings")
