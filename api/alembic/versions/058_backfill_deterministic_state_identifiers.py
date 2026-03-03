"""Backfill deterministic state identifiers before NOT NULL enforcement.

This migration:
1. Backfills NodeState.node_definition_id from Nodes.
2. Backfills NodePlacement.node_definition_id from Nodes.
3. Backfills LinkState.link_definition_id from Links using canonical endpoint
   normalization and, when needed, creates missing Link definitions.
4. Deletes irrecoverable orphan state rows (no matching parent definitions).
5. Enforces pre-constraint gates: no NULL FKs and no orphan FK references.

Revision ID: 058
Revises: 057
Create Date: 2026-03-03
"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.utils.link import canonicalize_link_endpoints, generate_link_name


# revision identifiers, used by Alembic.
revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fetch_nodes(conn):
    rows = conn.execute(
        text(
            """
            SELECT id, lab_id, gui_id, display_name, container_name, device
            FROM nodes
            """
        )
    ).mappings().all()

    by_lab_gui: dict[tuple[str, str], dict] = {}
    by_lab_container: dict[tuple[str, str], dict] = {}
    by_lab_display: dict[tuple[str, str], list[dict]] = {}
    node_device_by_name: dict[tuple[str, str], str | None] = {}

    for row in rows:
        by_lab_gui[(row["lab_id"], row["gui_id"])] = row
        by_lab_container[(row["lab_id"], row["container_name"])] = row
        by_lab_display.setdefault((row["lab_id"], row["display_name"]), []).append(row)
        node_device_by_name[(row["lab_id"], row["container_name"])] = row["device"]

    return by_lab_gui, by_lab_container, by_lab_display, node_device_by_name


def _resolve_node_for_name(
    *,
    lab_id: str,
    node_id: str | None,
    node_name: str | None,
    by_lab_gui: dict[tuple[str, str], dict],
    by_lab_container: dict[tuple[str, str], dict],
    by_lab_display: dict[tuple[str, str], list[dict]],
) -> dict | None:
    if node_id:
        found = by_lab_gui.get((lab_id, node_id))
        if found:
            return found
    if node_name:
        found = by_lab_container.get((lab_id, node_name))
        if found:
            return found
        display_matches = by_lab_display.get((lab_id, node_name), [])
        if len(display_matches) == 1:
            return display_matches[0]
    return None


def _backfill_node_states(
    conn,
    *,
    by_lab_gui: dict[tuple[str, str], dict],
    by_lab_container: dict[tuple[str, str], dict],
    by_lab_display: dict[tuple[str, str], list[dict]],
) -> tuple[int, int]:
    rows = conn.execute(
        text(
            """
            SELECT id, lab_id, node_id, node_name
            FROM node_states
            WHERE node_definition_id IS NULL
            """
        )
    ).mappings().all()

    updated = 0
    deleted = 0
    for row in rows:
        node = _resolve_node_for_name(
            lab_id=row["lab_id"],
            node_id=row["node_id"],
            node_name=row["node_name"],
            by_lab_gui=by_lab_gui,
            by_lab_container=by_lab_container,
            by_lab_display=by_lab_display,
        )
        if node:
            conn.execute(
                text(
                    """
                    UPDATE node_states
                    SET node_definition_id = :node_definition_id,
                        node_name = :node_name
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "node_definition_id": node["id"],
                    "node_name": node["container_name"],
                },
            )
            updated += 1
        else:
            conn.execute(
                text("DELETE FROM node_states WHERE id = :id"),
                {"id": row["id"]},
            )
            deleted += 1

    return updated, deleted


def _backfill_node_placements(
    conn,
    *,
    by_lab_gui: dict[tuple[str, str], dict],
    by_lab_container: dict[tuple[str, str], dict],
    by_lab_display: dict[tuple[str, str], list[dict]],
) -> tuple[int, int]:
    rows = conn.execute(
        text(
            """
            SELECT id, lab_id, node_name
            FROM node_placements
            WHERE node_definition_id IS NULL
            """
        )
    ).mappings().all()

    updated = 0
    deleted = 0
    for row in rows:
        node = _resolve_node_for_name(
            lab_id=row["lab_id"],
            node_id=row["node_name"],
            node_name=row["node_name"],
            by_lab_gui=by_lab_gui,
            by_lab_container=by_lab_container,
            by_lab_display=by_lab_display,
        )
        if node:
            conn.execute(
                text(
                    """
                    UPDATE node_placements
                    SET node_definition_id = :node_definition_id,
                        node_name = :node_name
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "node_definition_id": node["id"],
                    "node_name": node["container_name"],
                },
            )
            updated += 1
        else:
            conn.execute(
                text("DELETE FROM node_placements WHERE id = :id"),
                {"id": row["id"]},
            )
            deleted += 1

    return updated, deleted


def _build_link_definition_index(conn):
    link_rows = conn.execute(
        text(
            """
            SELECT
                l.id,
                l.lab_id,
                l.link_name,
                l.source_interface,
                l.target_interface,
                src.container_name AS source_node,
                src.device AS source_device,
                tgt.container_name AS target_node,
                tgt.device AS target_device
            FROM links l
            JOIN nodes src ON src.id = l.source_node_id
            JOIN nodes tgt ON tgt.id = l.target_node_id
            """
        )
    ).mappings().all()

    by_key: dict[tuple[str, str, str, str, str], str] = {}
    by_name: dict[tuple[str, str], str] = {}

    for row in link_rows:
        src_n, src_i, tgt_n, tgt_i = canonicalize_link_endpoints(
            row["source_node"],
            row["source_interface"],
            row["target_node"],
            row["target_interface"],
            source_device=row["source_device"],
            target_device=row["target_device"],
        )
        canonical_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        key = (row["lab_id"], src_n, src_i, tgt_n, tgt_i)
        by_key.setdefault(key, row["id"])
        by_name.setdefault((row["lab_id"], row["link_name"]), row["id"])
        by_name.setdefault((row["lab_id"], canonical_name), row["id"])

    return by_key, by_name


def _create_or_get_link_definition(
    conn,
    *,
    lab_id: str,
    src_node_id: str,
    tgt_node_id: str,
    src_n: str,
    src_i: str,
    tgt_n: str,
    tgt_i: str,
) -> str | None:
    link_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM links
            WHERE lab_id = :lab_id
              AND link_name = :link_name
            """
        ),
        {"lab_id": lab_id, "link_name": link_name},
    ).scalar()
    if existing:
        return existing

    new_id = str(uuid4())
    try:
        conn.execute(
            text(
                """
                INSERT INTO links (
                    id,
                    lab_id,
                    link_name,
                    source_node_id,
                    source_interface,
                    target_node_id,
                    target_interface
                )
                VALUES (
                    :id,
                    :lab_id,
                    :link_name,
                    :source_node_id,
                    :source_interface,
                    :target_node_id,
                    :target_interface
                )
                """
            ),
            {
                "id": new_id,
                "lab_id": lab_id,
                "link_name": link_name,
                "source_node_id": src_node_id,
                "source_interface": src_i,
                "target_node_id": tgt_node_id,
                "target_interface": tgt_i,
            },
        )
        return new_id
    except IntegrityError:
        return conn.execute(
            text(
                """
                SELECT id
                FROM links
                WHERE lab_id = :lab_id
                  AND link_name = :link_name
                """
            ),
            {"lab_id": lab_id, "link_name": link_name},
        ).scalar()


def _backfill_link_states(
    conn,
    *,
    by_lab_container: dict[tuple[str, str], dict],
    node_device_by_name: dict[tuple[str, str], str | None],
) -> tuple[int, int, int]:
    by_key, by_name = _build_link_definition_index(conn)
    rows = conn.execute(
        text(
            """
            SELECT
                id,
                lab_id,
                link_name,
                source_node,
                source_interface,
                target_node,
                target_interface
            FROM link_states
            WHERE link_definition_id IS NULL
            """
        )
    ).mappings().all()

    updated = 0
    created = 0
    deleted = 0

    for row in rows:
        src_dev = node_device_by_name.get((row["lab_id"], row["source_node"]))
        tgt_dev = node_device_by_name.get((row["lab_id"], row["target_node"]))
        src_n, src_i, tgt_n, tgt_i = canonicalize_link_endpoints(
            row["source_node"],
            row["source_interface"],
            row["target_node"],
            row["target_interface"],
            source_device=src_dev,
            target_device=tgt_dev,
        )
        canonical_name = generate_link_name(src_n, src_i, tgt_n, tgt_i)
        key = (row["lab_id"], src_n, src_i, tgt_n, tgt_i)

        link_definition_id = (
            by_key.get(key)
            or by_name.get((row["lab_id"], canonical_name))
            or by_name.get((row["lab_id"], row["link_name"]))
        )

        if not link_definition_id:
            src_node = by_lab_container.get((row["lab_id"], src_n))
            tgt_node = by_lab_container.get((row["lab_id"], tgt_n))
            if src_node and tgt_node:
                link_definition_id = _create_or_get_link_definition(
                    conn,
                    lab_id=row["lab_id"],
                    src_node_id=src_node["id"],
                    tgt_node_id=tgt_node["id"],
                    src_n=src_n,
                    src_i=src_i,
                    tgt_n=tgt_n,
                    tgt_i=tgt_i,
                )
                if link_definition_id:
                    by_key[key] = link_definition_id
                    by_name[(row["lab_id"], canonical_name)] = link_definition_id
                    created += 1

        if link_definition_id:
            conn.execute(
                text(
                    """
                    UPDATE link_states
                    SET link_definition_id = :link_definition_id,
                        link_name = :link_name,
                        source_node = :source_node,
                        source_interface = :source_interface,
                        target_node = :target_node,
                        target_interface = :target_interface
                    WHERE id = :id
                    """
                ),
                {
                    "id": row["id"],
                    "link_definition_id": link_definition_id,
                    "link_name": canonical_name,
                    "source_node": src_n,
                    "source_interface": src_i,
                    "target_node": tgt_n,
                    "target_interface": tgt_i,
                },
            )
            updated += 1
        else:
            conn.execute(
                text("DELETE FROM link_states WHERE id = :id"),
                {"id": row["id"]},
            )
            deleted += 1

    return updated, created, deleted


def _count(conn, sql: str) -> int:
    return int(conn.execute(text(sql)).scalar() or 0)


def _gate_counts(conn) -> dict[str, int]:
    return {
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


def upgrade() -> None:
    conn = op.get_bind()
    by_lab_gui, by_lab_container, by_lab_display, node_device_by_name = _fetch_nodes(conn)

    node_states_updated, node_states_deleted = _backfill_node_states(
        conn,
        by_lab_gui=by_lab_gui,
        by_lab_container=by_lab_container,
        by_lab_display=by_lab_display,
    )
    placements_updated, placements_deleted = _backfill_node_placements(
        conn,
        by_lab_gui=by_lab_gui,
        by_lab_container=by_lab_container,
        by_lab_display=by_lab_display,
    )
    link_states_updated, links_created, link_states_deleted = _backfill_link_states(
        conn,
        by_lab_container=by_lab_container,
        node_device_by_name=node_device_by_name,
    )

    gates = _gate_counts(conn)
    failures = {name: count for name, count in gates.items() if count > 0}
    if failures:
        raise RuntimeError(
            "Deterministic identifier backfill failed migration gate checks: "
            + ", ".join(f"{name}={count}" for name, count in sorted(failures.items()))
        )

    print(
        "Identifier backfill summary: "
        f"node_states(updated={node_states_updated}, deleted={node_states_deleted}), "
        f"node_placements(updated={placements_updated}, deleted={placements_deleted}), "
        f"link_states(updated={link_states_updated}, deleted={link_states_deleted}), "
        f"links_created={links_created}"
    )


def downgrade() -> None:
    # Data backfill is intentionally irreversible.
    pass
