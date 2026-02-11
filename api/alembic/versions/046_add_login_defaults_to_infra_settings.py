"""Add login defaults to infrastructure settings.

Revision ID: 046
Revises: 045
Create Date: 2026-02-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "infra_settings",
        sa.Column("login_dark_theme_id", sa.String(length=64), nullable=False, server_default="midnight"),
    )
    op.add_column(
        "infra_settings",
        sa.Column("login_dark_background_id", sa.String(length=64), nullable=False, server_default="floating-lanterns"),
    )
    op.add_column(
        "infra_settings",
        sa.Column("login_dark_background_opacity", sa.Integer(), nullable=False, server_default="50"),
    )
    op.add_column(
        "infra_settings",
        sa.Column("login_light_theme_id", sa.String(length=64), nullable=False, server_default="sakura-sumie"),
    )
    op.add_column(
        "infra_settings",
        sa.Column("login_light_background_id", sa.String(length=64), nullable=False, server_default="sakura-redux"),
    )
    op.add_column(
        "infra_settings",
        sa.Column("login_light_background_opacity", sa.Integer(), nullable=False, server_default="100"),
    )
    op.alter_column("infra_settings", "login_dark_theme_id", server_default=None)
    op.alter_column("infra_settings", "login_dark_background_id", server_default=None)
    op.alter_column("infra_settings", "login_dark_background_opacity", server_default=None)
    op.alter_column("infra_settings", "login_light_theme_id", server_default=None)
    op.alter_column("infra_settings", "login_light_background_id", server_default=None)
    op.alter_column("infra_settings", "login_light_background_opacity", server_default=None)


def downgrade() -> None:
    op.drop_column("infra_settings", "login_light_background_opacity")
    op.drop_column("infra_settings", "login_light_background_id")
    op.drop_column("infra_settings", "login_light_theme_id")
    op.drop_column("infra_settings", "login_dark_background_opacity")
    op.drop_column("infra_settings", "login_dark_background_id")
    op.drop_column("infra_settings", "login_dark_theme_id")

