"""Support multiple test paths per agent link pair.

Revision ID: 039
Revises: 038
Create Date: 2026-02-08

Changes:
- Backfill NULL test_path to "management"
- Drop unique constraint uq_agent_link_pair on (source_agent_id, target_agent_id)
- Make test_path NOT NULL with default "management"
- Create new unique constraint uq_agent_link_pair_path on (source_agent_id, target_agent_id, test_path)
"""
from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill NULL test_path values to "management"
    op.execute("UPDATE agent_links SET test_path = 'management' WHERE test_path IS NULL")

    # Drop old unique constraint (one record per agent pair)
    op.drop_constraint("uq_agent_link_pair", "agent_links", type_="unique")

    # Make test_path NOT NULL with default
    op.alter_column(
        "agent_links",
        "test_path",
        existing_type=sa.String(20),
        nullable=False,
        server_default="management",
    )

    # Create new unique constraint including test_path
    op.create_unique_constraint(
        "uq_agent_link_pair_path",
        "agent_links",
        ["source_agent_id", "target_agent_id", "test_path"],
    )


def downgrade() -> None:
    # Drop new unique constraint
    op.drop_constraint("uq_agent_link_pair_path", "agent_links", type_="unique")

    # Make test_path nullable again
    op.alter_column(
        "agent_links",
        "test_path",
        existing_type=sa.String(20),
        nullable=True,
        server_default=None,
    )

    # Restore old unique constraint
    op.create_unique_constraint(
        "uq_agent_link_pair",
        "agent_links",
        ["source_agent_id", "target_agent_id"],
    )
