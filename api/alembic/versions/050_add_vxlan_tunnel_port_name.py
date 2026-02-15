"""Add port_name column to vxlan_tunnels.

Stores the deterministic OVS port name (vxlan-<hash>) so the API
can declare full desired state to agents without recomputing names.

Backfills from existing LinkState records using the same MD5 hash
as compute_vxlan_port_name().

Revision ID: 050
Revises: 049
Create Date: 2026-02-14
"""
from typing import Sequence, Union
import hashlib

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _compute_port_name(lab_id: str, link_name: str) -> str:
    """Match compute_vxlan_port_name() from agent_client.py."""
    combined = f"{lab_id}:{link_name}"
    link_hash = hashlib.md5(combined.encode()).hexdigest()[:8]
    return f"vxlan-{link_hash}"


def upgrade() -> None:
    op.add_column(
        "vxlan_tunnels",
        sa.Column("port_name", sa.String(), nullable=True),
    )

    # Backfill from joined LinkState records
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT vt.id, ls.lab_id, ls.link_name "
            "FROM vxlan_tunnels vt "
            "JOIN link_states ls ON vt.link_state_id = ls.id "
            "WHERE vt.link_state_id IS NOT NULL"
        )
    ).fetchall()

    for row in rows:
        tunnel_id, lab_id, link_name = row
        port_name = _compute_port_name(str(lab_id), link_name)
        conn.execute(
            text("UPDATE vxlan_tunnels SET port_name = :pn WHERE id = :tid"),
            {"pn": port_name, "tid": tunnel_id},
        )


def downgrade() -> None:
    op.drop_column("vxlan_tunnels", "port_name")
