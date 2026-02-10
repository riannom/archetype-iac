"""Add RBAC fields to users and permissions tables.

Revision ID: 041
Revises: 040
Create Date: 2026-02-10

Adds:
- username column to users (nullable initially for backfill)
- global_role column to users (defaults to 'operator')
- unique constraint on permissions(lab_id, user_id) after dedup
"""
from alembic import op
import sqlalchemy as sa

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add username column (nullable initially — backfill in 042)
    op.add_column("users", sa.Column("username", sa.String(32), nullable=True))
    op.create_index("ix_users_username", "users", ["username"])

    # 2. Add global_role column
    op.add_column(
        "users",
        sa.Column("global_role", sa.String(20), server_default="operator", nullable=False),
    )

    # 3. Deduplicate permissions rows where (lab_id, user_id) collide
    #    Keep the most recent row (highest created_at) for each pair
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            DELETE FROM permissions
            WHERE id NOT IN (
                SELECT DISTINCT ON (lab_id, user_id) id
                FROM permissions
                ORDER BY lab_id, user_id, created_at DESC
            )
        """)
    )

    # 4. Add unique constraint on permissions(lab_id, user_id)
    op.create_unique_constraint("uq_permission_lab_user", "permissions", ["lab_id", "user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_permission_lab_user", "permissions", type_="unique")
    op.drop_column("users", "global_role")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
