from __future__ import annotations

from pathlib import Path
import importlib.util

import pytest


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    def __init__(self, scalar_values: list[int] | None = None) -> None:
        self.scalar_values = scalar_values or [0] * 6
        self.calls = 0

    def execute(self, _sql):
        idx = min(self.calls, len(self.scalar_values) - 1)
        self.calls += 1
        return _FakeResult(self.scalar_values[idx])


class _FakeOp:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.dropped: list[tuple[str, str, str]] = []
        self.created: list[tuple[str, str, str, str]] = []
        self.altered: list[tuple[str, str, bool]] = []

    def get_bind(self):
        return self.conn

    def drop_constraint(self, name: str, table_name: str, type_: str) -> None:
        self.dropped.append((name, table_name, type_))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols,
        remote_cols,
        ondelete: str,
    ) -> None:
        self.created.append((name, source_table, referent_table, ondelete))

    def alter_column(self, table_name: str, column_name: str, **kwargs) -> None:
        self.altered.append((table_name, column_name, bool(kwargs.get("nullable"))))


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "059_enforce_state_identifier_constraints.py"
    )
    spec = importlib.util.spec_from_file_location("alembic_059_identifier_constraints", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_enforces_not_null_and_cascade(monkeypatch) -> None:
    module = _load_migration_module()
    fake_conn = _FakeConn([0, 0, 0, 0, 0, 0])
    fake_op = _FakeOp(fake_conn)
    monkeypatch.setattr(module, "op", fake_op)
    dropped_columns: list[tuple[str, str]] = []

    def _fake_drop_fk_constraints(_conn, *, table_name: str, column_name: str) -> None:
        dropped_columns.append((table_name, column_name))

    monkeypatch.setattr(module, "_drop_fk_constraints", _fake_drop_fk_constraints)

    module.upgrade()

    assert set(dropped_columns) == {
        ("node_states", "node_definition_id"),
        ("link_states", "link_definition_id"),
        ("node_placements", "node_definition_id"),
    }

    assert {row[3] for row in fake_op.created} == {"CASCADE"}
    altered_cols = {(table, column, nullable) for table, column, nullable in fake_op.altered}
    assert ("node_states", "node_definition_id", False) in altered_cols
    assert ("link_states", "link_definition_id", False) in altered_cols
    assert ("node_placements", "node_definition_id", False) in altered_cols


def test_upgrade_fails_gate_when_nulls_remain(monkeypatch) -> None:
    module = _load_migration_module()
    # First gate query (node_states_null) returns non-zero.
    fake_conn = _FakeConn([1, 0, 0, 0, 0, 0])
    fake_op = _FakeOp(fake_conn)
    monkeypatch.setattr(module, "op", fake_op)

    with pytest.raises(RuntimeError):
        module.upgrade()
