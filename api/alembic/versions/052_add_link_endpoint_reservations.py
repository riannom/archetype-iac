"""Add link endpoint reservations for strict 1:1 link semantics.

Revision ID: 052
Revises: 051
Create Date: 2026-02-16
"""
from typing import Sequence, Union
from uuid import uuid4
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "link_endpoint_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lab_id", sa.String(length=36), nullable=False),
        sa.Column("link_state_id", sa.String(length=36), nullable=False),
        sa.Column("node_name", sa.String(length=100), nullable=False),
        sa.Column("interface_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["lab_id"], ["labs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["link_state_id"], ["link_states.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lab_id",
            "node_name",
            "interface_name",
            name="uq_link_endpoint_reservation_endpoint",
        ),
    )
    op.create_index(
        "ix_link_endpoint_reservations_lab_id",
        "link_endpoint_reservations",
        ["lab_id"],
    )
    op.create_index(
        "ix_link_endpoint_reservations_link_state_id",
        "link_endpoint_reservations",
        ["link_state_id"],
    )

    # Backfill current desired-up links with normalized endpoint reservations.
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, lab_id, source_node, source_interface, target_node, target_interface "
            "FROM link_states "
            "WHERE desired_state = 'up'"
        )
    ).fetchall()

    def _normalize_interface(name: str) -> str:
        value = (name or "").strip()
        if not value:
            return value
        m = re.match(r"^(ethernet|eth)(\d+)$", value, flags=re.IGNORECASE)
        if m:
            return f"eth{m.group(2)}"
        return value.lower()

    for row in rows:
        link_state_id, lab_id, src_node, src_iface, tgt_node, tgt_iface = row
        for node_name, interface_name in (
            (src_node, _normalize_interface(str(src_iface))),
            (tgt_node, _normalize_interface(str(tgt_iface))),
        ):
            try:
                conn.execute(
                    text(
                        "INSERT INTO link_endpoint_reservations "
                        "(id, lab_id, link_state_id, node_name, interface_name) "
                        "VALUES (:id, :lab_id, :link_state_id, :node_name, :interface_name)"
                    ),
                    {
                        "id": str(uuid4()),
                        "lab_id": str(lab_id),
                        "link_state_id": str(link_state_id),
                        "node_name": str(node_name),
                        "interface_name": str(interface_name),
                    },
                )
            except Exception:
                # If legacy data has endpoint conflicts, keep migration moving;
                # runtime claim logic will surface and prevent new conflicts.
                continue


def downgrade() -> None:
    op.drop_index("ix_link_endpoint_reservations_link_state_id", table_name="link_endpoint_reservations")
    op.drop_index("ix_link_endpoint_reservations_lab_id", table_name="link_endpoint_reservations")
    op.drop_table("link_endpoint_reservations")
