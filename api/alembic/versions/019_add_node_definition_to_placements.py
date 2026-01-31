"""Add node_definition_id to node_placements.

This enables FK-first lookups for placement records, making state enforcement
and reconciliation more reliable by not depending solely on string matching.

Revision ID: 019
Revises: 018
Create Date: 2026-01-31
"""
from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "node_placements",
        sa.Column("node_definition_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_node_placements_node_definition",
        "node_placements", "nodes",
        ["node_definition_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_node_placements_node_definition_id",
        "node_placements", ["node_definition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_node_placements_node_definition_id", table_name="node_placements")
    op.drop_constraint("fk_node_placements_node_definition", "node_placements", type_="foreignkey")
    op.drop_column("node_placements", "node_definition_id")
