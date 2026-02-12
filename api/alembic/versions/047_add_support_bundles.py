"""Add support bundles table.

Revision ID: 047
Revises: 046
Create Date: 2026-02-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_bundles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("include_configs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pii_safe", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("time_window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("options_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("incident_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_bundles_user_id", "support_bundles", ["user_id"])
    op.create_index("ix_support_bundles_status", "support_bundles", ["status"])
    op.create_index("ix_support_bundles_created_at", "support_bundles", ["created_at"])

    op.alter_column("support_bundles", "status", server_default=None)
    op.alter_column("support_bundles", "include_configs", server_default=None)
    op.alter_column("support_bundles", "pii_safe", server_default=None)
    op.alter_column("support_bundles", "time_window_hours", server_default=None)
    op.alter_column("support_bundles", "options_json", server_default=None)
    op.alter_column("support_bundles", "incident_json", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_support_bundles_created_at", table_name="support_bundles")
    op.drop_index("ix_support_bundles_status", table_name="support_bundles")
    op.drop_index("ix_support_bundles_user_id", table_name="support_bundles")
    op.drop_table("support_bundles")
