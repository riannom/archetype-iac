"""Add managed_interface_id FK to nodes table.

Revision ID: 037
Revises: 036
Create Date: 2026-02-08

Adds:
- nodes.managed_interface_id: FK to agent_managed_interfaces for external network nodes
"""
from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nodes",
        sa.Column("managed_interface_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_nodes_managed_interface_id",
        "nodes",
        "agent_managed_interfaces",
        ["managed_interface_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_nodes_managed_interface_id", "nodes", type_="foreignkey")
    op.drop_column("nodes", "managed_interface_id")
