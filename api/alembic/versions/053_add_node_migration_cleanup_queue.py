"""Add deferred cleanup queue for node migrations.

Revision ID: 053
Revises: 052
Create Date: 2026-02-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_migration_cleanups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lab_id", sa.String(length=36), nullable=False),
        sa.Column("node_name", sa.String(length=100), nullable=False),
        sa.Column("old_host_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="docker"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["lab_id"], ["labs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["old_host_id"], ["hosts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lab_id",
            "node_name",
            "old_host_id",
            name="uq_node_migration_cleanup_target",
        ),
    )
    op.create_index(
        "ix_node_migration_cleanups_lab_id",
        "node_migration_cleanups",
        ["lab_id"],
    )
    op.create_index(
        "ix_node_migration_cleanups_node_name",
        "node_migration_cleanups",
        ["node_name"],
    )
    op.create_index(
        "ix_node_migration_cleanups_old_host_id",
        "node_migration_cleanups",
        ["old_host_id"],
    )
    op.create_index(
        "ix_node_migration_cleanups_status",
        "node_migration_cleanups",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_node_migration_cleanups_status", table_name="node_migration_cleanups")
    op.drop_index("ix_node_migration_cleanups_old_host_id", table_name="node_migration_cleanups")
    op.drop_index("ix_node_migration_cleanups_node_name", table_name="node_migration_cleanups")
    op.drop_index("ix_node_migration_cleanups_lab_id", table_name="node_migration_cleanups")
    op.drop_table("node_migration_cleanups")
