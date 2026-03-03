from __future__ import annotations

from pathlib import Path
import importlib.util

from sqlalchemy import create_engine, text


class _FakeOp:
    def __init__(self, conn) -> None:
        self._conn = conn

    def get_bind(self):
        return self._conn


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "058_backfill_deterministic_state_identifiers.py"
    )
    spec = importlib.util.spec_from_file_location("alembic_058_identifier_backfill", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_schema(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE nodes (
                id TEXT PRIMARY KEY,
                lab_id TEXT NOT NULL,
                gui_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                container_name TEXT NOT NULL,
                device TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE node_states (
                id TEXT PRIMARY KEY,
                lab_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                node_definition_id TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE node_placements (
                id TEXT PRIMARY KEY,
                lab_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                node_definition_id TEXT,
                host_id TEXT
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE links (
                id TEXT PRIMARY KEY,
                lab_id TEXT NOT NULL,
                link_name TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                source_interface TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                target_interface TEXT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE link_states (
                id TEXT PRIMARY KEY,
                lab_id TEXT NOT NULL,
                link_name TEXT NOT NULL,
                source_node TEXT NOT NULL,
                source_interface TEXT NOT NULL,
                target_node TEXT NOT NULL,
                target_interface TEXT NOT NULL,
                link_definition_id TEXT
            )
            """
        )
    )


def test_upgrade_backfills_identifiers_and_cleans_orphans(monkeypatch) -> None:
    module = _load_migration_module()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)

        conn.execute(
            text(
                """
                INSERT INTO nodes (id, lab_id, gui_id, display_name, container_name, device) VALUES
                ('node-1', 'lab-1', 'g1', 'R1', 'r1', 'ceos'),
                ('node-2', 'lab-1', 'g2', 'R2', 'r2', 'cisco_iosv')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO node_states (id, lab_id, node_id, node_name, node_definition_id) VALUES
                ('ns-1', 'lab-1', 'g1', 'g1', NULL),
                ('ns-orphan', 'lab-1', 'missing', 'missing', NULL)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO node_placements (id, lab_id, node_name, node_definition_id, host_id) VALUES
                ('np-1', 'lab-1', 'R1', NULL, 'host-1'),
                ('np-orphan', 'lab-1', 'ghost', NULL, 'host-1')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO link_states (
                    id,
                    lab_id,
                    link_name,
                    source_node,
                    source_interface,
                    target_node,
                    target_interface,
                    link_definition_id
                ) VALUES
                ('ls-1', 'lab-1', 'R1:Ethernet1-R2:GigabitEthernet0/1', 'r1', 'Ethernet1', 'r2', 'GigabitEthernet0/1', NULL),
                ('ls-orphan', 'lab-1', 'ghost:eth1-r2:eth1', 'ghost', 'eth1', 'r2', 'eth1', NULL)
                """
            )
        )

        monkeypatch.setattr(module, "op", _FakeOp(conn))
        module.upgrade()

        ns = conn.execute(
            text("SELECT node_name, node_definition_id FROM node_states WHERE id = 'ns-1'")
        ).mappings().one()
        assert ns["node_name"] == "r1"
        assert ns["node_definition_id"] == "node-1"
        assert conn.execute(text("SELECT COUNT(*) FROM node_states WHERE id = 'ns-orphan'")).scalar() == 0

        np = conn.execute(
            text("SELECT node_name, node_definition_id FROM node_placements WHERE id = 'np-1'")
        ).mappings().one()
        assert np["node_name"] == "r1"
        assert np["node_definition_id"] == "node-1"
        assert conn.execute(text("SELECT COUNT(*) FROM node_placements WHERE id = 'np-orphan'")).scalar() == 0

        links = conn.execute(
            text(
                """
                SELECT link_name, source_interface, target_interface
                FROM links
                WHERE lab_id = 'lab-1'
                """
            )
        ).mappings().all()
        assert len(links) == 1
        assert links[0]["link_name"] == "r1:eth1-r2:eth2"

        ls = conn.execute(
            text(
                """
                SELECT link_name, source_interface, target_interface, link_definition_id
                FROM link_states
                WHERE id = 'ls-1'
                """
            )
        ).mappings().one()
        assert ls["link_name"] == "r1:eth1-r2:eth2"
        assert ls["source_interface"] == "eth1"
        assert ls["target_interface"] == "eth2"
        assert ls["link_definition_id"] is not None
        assert conn.execute(text("SELECT COUNT(*) FROM link_states WHERE id = 'ls-orphan'")).scalar() == 0
