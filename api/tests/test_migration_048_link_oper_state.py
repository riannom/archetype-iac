from __future__ import annotations

from pathlib import Path
import importlib.util


class _FakeOp:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.altered: list[tuple[str, str]] = []
        self.executed_sql: list[str] = []

    def add_column(self, table_name, column) -> None:
        assert table_name == "link_states"
        self.added.append(column.name)

    def alter_column(self, table_name, column_name, **kwargs) -> None:
        assert table_name == "link_states"
        self.altered.append((column_name, str(kwargs.get("server_default"))))

    def execute(self, sql) -> None:
        self.executed_sql.append(str(sql))

    def drop_column(self, table_name, column_name) -> None:  # pragma: no cover - downgrade path not used
        pass


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "048_add_link_operational_state_fields.py"
    )
    spec = importlib.util.spec_from_file_location("alembic_048_oper_state", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_backfills_oper_state_from_carrier(monkeypatch) -> None:
    module = _load_migration_module()
    fake_op = _FakeOp()
    monkeypatch.setattr(module, "op", fake_op)

    module.upgrade()

    assert {
        "source_oper_state",
        "target_oper_state",
        "source_oper_reason",
        "target_oper_reason",
        "source_last_change_at",
        "target_last_change_at",
        "oper_epoch",
    }.issubset(set(fake_op.added))

    update_sql = "\n".join(fake_op.executed_sql)
    assert "UPDATE link_states" in update_sql
    assert "source_carrier_state = 'on'" in update_sql
    assert "target_carrier_state = 'on'" in update_sql
    assert "source_oper_state = CASE" in update_sql
    assert "target_oper_state = CASE" in update_sql

    altered_cols = {name for name, _ in fake_op.altered}
    assert {"source_oper_state", "target_oper_state", "oper_epoch"}.issubset(altered_cols)
