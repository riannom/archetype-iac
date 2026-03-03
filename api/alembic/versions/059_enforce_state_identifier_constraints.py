"""Enforce NOT NULL + CASCADE constraints for deterministic state identifiers.

Revision ID: 059
Revises: 058
Create Date: 2026-03-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _count(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def _assert_migration_gates(conn) -> None:
    checks = {
        "node_states_null": _count(
            conn,
            "SELECT COUNT(*) FROM node_states WHERE node_definition_id IS NULL",
        ),
        "node_placements_null": _count(
            conn,
            "SELECT COUNT(*) FROM node_placements WHERE node_definition_id IS NULL",
        ),
        "link_states_null": _count(
            conn,
            "SELECT COUNT(*) FROM link_states WHERE link_definition_id IS NULL",
        ),
        "node_states_orphan_fk": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM node_states ns
            LEFT JOIN nodes n ON ns.node_definition_id = n.id
            WHERE ns.node_definition_id IS NOT NULL
              AND n.id IS NULL
            """,
        ),
        "node_placements_orphan_fk": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM node_placements np
            LEFT JOIN nodes n ON np.node_definition_id = n.id
            WHERE np.node_definition_id IS NOT NULL
              AND n.id IS NULL
            """,
        ),
        "link_states_orphan_fk": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM link_states ls
            LEFT JOIN links l ON ls.link_definition_id = l.id
            WHERE ls.link_definition_id IS NOT NULL
              AND l.id IS NULL
            """,
        ),
    }
    failures = {name: count for name, count in checks.items() if count > 0}
    if failures:
        raise RuntimeError(
            "Cannot enforce deterministic identifier constraints; "
            + ", ".join(f"{name}={count}" for name, count in sorted(failures.items()))
        )


def _drop_fk_constraints(conn, *, table_name: str, column_name: str) -> None:
    rows = conn.execute(
        text(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = t.oid
            WHERE c.contype = 'f'
              AND n.nspname = current_schema()
              AND t.relname = :table_name
              AND a.attname = :column_name
              AND a.attnum = ANY(c.conkey)
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalars().all()

    for name in dict.fromkeys(rows):
        op.drop_constraint(name, table_name, type_="foreignkey")


def upgrade() -> None:
    conn = op.get_bind()
    _assert_migration_gates(conn)

    _drop_fk_constraints(conn, table_name="node_states", column_name="node_definition_id")
    op.create_foreign_key(
        "fk_node_states_node_definition",
        "node_states",
        "nodes",
        ["node_definition_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "node_states",
        "node_definition_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    _drop_fk_constraints(conn, table_name="link_states", column_name="link_definition_id")
    op.create_foreign_key(
        "fk_link_states_link_definition",
        "link_states",
        "links",
        ["link_definition_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "link_states",
        "link_definition_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    _drop_fk_constraints(conn, table_name="node_placements", column_name="node_definition_id")
    op.create_foreign_key(
        "fk_node_placements_node_definition",
        "node_placements",
        "nodes",
        ["node_definition_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "node_placements",
        "node_definition_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "node_placements",
        "node_definition_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    conn = op.get_bind()
    _drop_fk_constraints(conn, table_name="node_placements", column_name="node_definition_id")
    op.create_foreign_key(
        "fk_node_placements_node_definition",
        "node_placements",
        "nodes",
        ["node_definition_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "link_states",
        "link_definition_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    _drop_fk_constraints(conn, table_name="link_states", column_name="link_definition_id")
    op.create_foreign_key(
        "fk_link_states_link_definition",
        "link_states",
        "links",
        ["link_definition_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "node_states",
        "node_definition_id",
        existing_type=sa.String(length=36),
        nullable=True,
    )
    _drop_fk_constraints(conn, table_name="node_states", column_name="node_definition_id")
    op.create_foreign_key(
        "fk_node_states_node_definition",
        "node_states",
        "nodes",
        ["node_definition_id"],
        ["id"],
        ondelete="SET NULL",
    )
