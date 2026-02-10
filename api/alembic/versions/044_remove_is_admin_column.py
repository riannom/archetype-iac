"""Remove deprecated is_admin column from users table.

The global_role field now fully replaces is_admin.
"""

from alembic import op
import sqlalchemy as sa

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "is_admin")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Restore is_admin from global_role
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE users SET is_admin = true WHERE global_role IN ('super_admin', 'admin')")
    )
