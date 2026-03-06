"""Tests for cleanup_base.py — CleanupResult, CleanupRunner, and valid-ID queries."""
from __future__ import annotations

import asyncio


from app.tasks.cleanup_base import CleanupResult, CleanupRunner, get_valid_host_ids, get_valid_lab_ids, get_valid_user_ids


# ---------------------------------------------------------------------------
# CleanupResult dataclass
# ---------------------------------------------------------------------------


class TestCleanupResult:
    def test_success_when_no_errors(self):
        result = CleanupResult(task_name="test_task", deleted=5)
        assert result.success is True

    def test_success_false_when_errors(self):
        result = CleanupResult(task_name="test_task", errors=["something broke"])
        assert result.success is False

    def test_to_dict_contains_all_fields(self):
        result = CleanupResult(
            task_name="clean_orphans",
            deleted=3,
            errors=["err1"],
            details={"extra": "info"},
            duration_ms=123.456,
        )
        d = result.to_dict()
        assert d["task_name"] == "clean_orphans"
        assert d["deleted"] == 3
        assert d["errors"] == ["err1"]
        assert d["details"] == {"extra": "info"}
        assert d["duration_ms"] == 123.5  # rounded to 1 decimal
        assert d["success"] is False

    def test_to_dict_success_true(self):
        result = CleanupResult(task_name="ok_task", deleted=0)
        d = result.to_dict()
        assert d["success"] is True
        assert d["errors"] == []

    def test_default_field_values(self):
        result = CleanupResult(task_name="defaults")
        assert result.deleted == 0
        assert result.errors == []
        assert result.details == {}
        assert result.duration_ms == 0


# ---------------------------------------------------------------------------
# CleanupRunner
# ---------------------------------------------------------------------------


class TestCleanupRunner:
    def test_run_task_success(self):
        """run_task returns the result from a successful async task."""

        async def _good_task():
            return CleanupResult(task_name="good", deleted=7)

        runner = CleanupRunner()
        result = asyncio.get_event_loop().run_until_complete(runner.run_task(_good_task))
        assert result.task_name == "good"
        assert result.deleted == 7
        assert result.success is True
        assert result.duration_ms > 0

    def test_run_task_captures_exception(self):
        """run_task catches exceptions and returns an error result."""

        async def _bad_task():
            raise RuntimeError("kaboom")

        runner = CleanupRunner()
        result = asyncio.get_event_loop().run_until_complete(runner.run_task(_bad_task))
        assert result.success is False
        assert "kaboom" in result.errors[0]
        assert result.duration_ms > 0

    def test_run_tasks_sequential(self):
        """run_tasks executes multiple tasks and returns all results in order."""
        call_order = []

        async def _task_a():
            call_order.append("a")
            return CleanupResult(task_name="a", deleted=1)

        async def _task_b():
            call_order.append("b")
            return CleanupResult(task_name="b", deleted=2)

        runner = CleanupRunner()
        results = asyncio.get_event_loop().run_until_complete(
            runner.run_tasks([_task_a, _task_b])
        )
        assert len(results) == 2
        assert results[0].task_name == "a"
        assert results[1].task_name == "b"
        assert call_order == ["a", "b"]

    def test_run_task_passes_args(self):
        """run_task forwards positional and keyword arguments to the task."""

        async def _task_with_args(x, y, extra=None):
            return CleanupResult(
                task_name="args_task",
                deleted=x + y,
                details={"extra": extra},
            )

        runner = CleanupRunner()
        result = asyncio.get_event_loop().run_until_complete(
            runner.run_task(_task_with_args, 3, 4, extra="hello")
        )
        assert result.deleted == 7
        assert result.details["extra"] == "hello"


# ---------------------------------------------------------------------------
# Valid-ID query helpers (require test_db)
# ---------------------------------------------------------------------------


class TestGetValidIds:
    def test_get_valid_lab_ids(self, test_db, sample_lab):
        ids = get_valid_lab_ids(test_db)
        assert str(sample_lab.id) in ids

    def test_get_valid_lab_ids_empty(self, test_db):
        ids = get_valid_lab_ids(test_db)
        assert ids == set()

    def test_get_valid_host_ids(self, test_db, sample_host):
        ids = get_valid_host_ids(test_db)
        assert sample_host.id in ids

    def test_get_valid_user_ids(self, test_db, test_user):
        ids = get_valid_user_ids(test_db)
        assert test_user.id in ids
