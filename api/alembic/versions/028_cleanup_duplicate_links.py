"""Cleanup duplicate link_states with different naming conventions.

This migration removes duplicate link entries where the same link
exists with different interface naming (e.g., "Ethernet1" vs "eth1").

The oldest entry (by created_at) is kept, duplicates are deleted.

Revision ID: 028
Revises: 027
Create Date: 2026-02-02
"""
import re

from alembic import op
from sqlalchemy import text

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def normalize_interface(name: str) -> str:
    """Normalize interface name to eth{n} format.

    Examples:
        Ethernet1 -> eth1
        ethernet-1/1 -> eth1
        GigabitEthernet0/0/0/1 -> eth1
        ge-0/0/1 -> eth1
        eth1 -> eth1
    """
    if not name:
        return name

    # Try to extract the interface number
    # Pattern priority: last number in the string is usually the interface index
    patterns = [
        r"[Ee]thernet[-/]?(\d+)",  # Ethernet1, ethernet-1/1
        r"[Gg]e[-/]?\d+/\d+/(\d+)",  # ge-0/0/0
        r"[Gg]igabit[Ee]thernet\d+/\d+/\d+/(\d+)",  # GigabitEthernet0/0/0/0
        r"eth(\d+)",  # eth1
    ]

    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            return f"eth{match.group(1)}"

    # Fallback: try to find any number
    match = re.search(r"(\d+)$", name)
    if match:
        return f"eth{match.group(1)}"

    return name


def upgrade() -> None:
    """Deduplicate link_states with Ethernet vs eth naming."""
    conn = op.get_bind()

    # Get all link_states ordered by created_at (oldest first)
    result = conn.execute(text("""
        SELECT id, lab_id, link_name, source_node, source_interface,
               target_node, target_interface, actual_state, vlan_tag, created_at
        FROM link_states
        ORDER BY created_at ASC
    """))

    links = result.fetchall()
    seen: dict[str, str] = {}  # normalized_key -> first link id
    duplicates: list[str] = []

    for link in links:
        link_id = link[0]
        lab_id = link[1]
        source_node = link[3]
        source_interface = link[4]
        target_node = link[5]
        target_interface = link[6]

        # Normalize interface names
        source_if = normalize_interface(source_interface)
        target_if = normalize_interface(target_interface)

        # Create canonical key (sorted to handle direction)
        endpoints = sorted([
            (source_node, source_if),
            (target_node, target_if)
        ])
        key = f"{lab_id}:{endpoints[0][0]}:{endpoints[0][1]}-{endpoints[1][0]}:{endpoints[1][1]}"

        if key in seen:
            # This is a duplicate - mark for deletion
            duplicates.append(link_id)
        else:
            # First occurrence - keep it
            seen[key] = link_id

    # Delete duplicates
    if duplicates:
        # Delete in batches to avoid parameter limits
        batch_size = 100
        for i in range(0, len(duplicates), batch_size):
            batch = duplicates[i:i + batch_size]
            placeholders = ", ".join([f":id{j}" for j in range(len(batch))])
            params = {f"id{j}": batch[j] for j in range(len(batch))}
            conn.execute(
                text(f"DELETE FROM link_states WHERE id IN ({placeholders})"),
                params
            )

    print(f"Removed {len(duplicates)} duplicate link_states (kept {len(seen)} unique)")


def downgrade() -> None:
    # Cannot restore deleted duplicates
    pass
