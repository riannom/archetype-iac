"""Fix VxlanTunnel unique constraint for trunk VTEP model.

The trunk VTEP model (commit 0223ef0) changed cross-host links to share
a single VTEP per host-pair with VLAN tags for isolation. VNI is no longer
unique per link - multiple links between the same host pair share the same VNI.

This migration changes the unique constraint from (lab_id, vni) to
(lab_id, link_state_id) since each link_state should have at most one
tunnel record.

Revision ID: 030
Revises: 029
Create Date: 2026-02-03
"""
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old constraint that requires unique VNI per lab
    op.drop_constraint("uq_vxlan_tunnel_lab_vni", "vxlan_tunnels", type_="unique")

    # Add new constraint that ensures one tunnel record per link_state
    op.create_unique_constraint(
        "uq_vxlan_tunnel_link_state",
        "vxlan_tunnels",
        ["link_state_id"],
    )


def downgrade() -> None:
    # Drop the new constraint
    op.drop_constraint("uq_vxlan_tunnel_link_state", "vxlan_tunnels", type_="unique")

    # Restore the old constraint
    op.create_unique_constraint(
        "uq_vxlan_tunnel_lab_vni",
        "vxlan_tunnels",
        ["lab_id", "vni"],
    )
