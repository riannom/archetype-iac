"""Backfill usernames from email and set global_role from is_admin.

Revision ID: 042
Revises: 041
Create Date: 2026-02-10

- Sets global_role='super_admin' where is_admin=true
- Sets global_role='operator' where is_admin=false
- Derives username from email prefix (lowercase, validated)
- Makes username NOT NULL with unique constraint
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None

USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{2,31}$")


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Set global_role from is_admin
    conn.execute(sa.text("UPDATE users SET global_role = 'super_admin' WHERE is_admin = true"))
    conn.execute(sa.text("UPDATE users SET global_role = 'operator' WHERE is_admin = false"))

    # 2. Derive usernames from email prefixes
    users = conn.execute(sa.text("SELECT id, email FROM users ORDER BY created_at")).fetchall()
    used_usernames: set[str] = set()

    for user_id, email in users:
        # Extract prefix from email
        prefix = email.split("@")[0].lower() if "@" in email else email.lower()
        # Replace invalid chars with underscores
        prefix = re.sub(r"[^a-zA-Z0-9._-]", "_", prefix)
        # Ensure starts with letter
        if not prefix or not prefix[0].isalpha():
            prefix = "user_" + prefix
        # Truncate to 32 chars
        prefix = prefix[:32]
        # Pad if too short (minimum 3 chars)
        while len(prefix) < 3:
            prefix = prefix + "_"

        candidate = prefix
        if not USERNAME_RE.match(candidate) or candidate in used_usernames:
            # Use a safe fallback
            import uuid
            candidate = f"user_{uuid.uuid4().hex[:8]}"

        # Handle collisions
        counter = 2
        base = candidate
        while candidate in used_usernames:
            suffix = f"_{counter}"
            candidate = base[: 32 - len(suffix)] + suffix
            counter += 1

        used_usernames.add(candidate)
        conn.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :id"),
            {"username": candidate, "id": user_id},
        )

    # 3. Make username NOT NULL
    op.alter_column("users", "username", nullable=False)

    # 4. Add unique constraint
    op.create_unique_constraint("uq_users_username", "users", ["username"])


def downgrade() -> None:
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.alter_column("users", "username", nullable=True)
    # Note: we don't clear usernames on downgrade — they're harmless nullable data
