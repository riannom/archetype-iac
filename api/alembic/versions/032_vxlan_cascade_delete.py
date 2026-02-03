"""Change VxlanTunnel.link_state_id to CASCADE delete.

When a LinkState is deleted, the associated VxlanTunnel record should also
be deleted automatically. Previously this was SET NULL which left orphaned
tunnel records that required manual cleanup.

Revision ID: 032
Revises: 031
Create Date: 2026-02-03
"""
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing foreign key constraint
    op.drop_constraint("vxlan_tunnels_link_state_id_fkey", "vxlan_tunnels", type_="foreignkey")

    # Recreate with CASCADE delete
    op.create_foreign_key(
        "vxlan_tunnels_link_state_id_fkey",
        "vxlan_tunnels",
        "link_states",
        ["link_state_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Drop CASCADE constraint
    op.drop_constraint("vxlan_tunnels_link_state_id_fkey", "vxlan_tunnels", type_="foreignkey")

    # Recreate with SET NULL
    op.create_foreign_key(
        "vxlan_tunnels_link_state_id_fkey",
        "vxlan_tunnels",
        "link_states",
        ["link_state_id"],
        ["id"],
        ondelete="SET NULL",
    )
