"""Tests for app/tasks/test_runner.py — Lab verification test runner."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus


def _fake_get_session(session):
    """Create a fake get_session context manager that yields the test session."""
    @contextmanager
    def _get_session():
        yield session
    return _get_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def runner_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a running lab for test runner tests."""
    lab = models.Lab(
        name="Runner Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/runner-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def runner_job(
    test_db: Session,
    runner_lab: models.Lab,
    test_user: models.User,
    tmp_path,
) -> models.Job:
    """Create a queued verification job."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=runner_lab.id,
        user_id=test_user.id,
        action="verify",
        status="queued",
        log_path=str(tmp_path / "verify.json"),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture()
def runner_nodes(
    test_db: Session, runner_lab: models.Lab,
) -> list[models.NodeState]:
    """Create node states for the runner lab."""
    nodes = []
    for name in ("R1", "R2"):
        ns = models.NodeState(
            lab_id=runner_lab.id,
            node_id=name.lower(),
            node_name=name,
            desired_state="running",
            actual_state="running",
            is_ready=True,
        )
        test_db.add(ns)
        nodes.append(ns)
    test_db.commit()
    for ns in nodes:
        test_db.refresh(ns)
    return nodes


@pytest.fixture()
def runner_link(
    test_db: Session, runner_lab: models.Lab,
) -> models.LinkState:
    """Create a link state for the runner lab."""
    ls = models.LinkState(
        id=str(uuid4()),
        lab_id=runner_lab.id,
        link_name="R1:eth1-R2:eth1",
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


# ---------------------------------------------------------------------------
# TestNodeState
# ---------------------------------------------------------------------------

class TestNodeState:
    """Tests for node_state test type in _run_single_test."""

    @pytest.mark.asyncio
    async def test_node_state_match(
        self, test_db: Session, runner_lab: models.Lab, runner_nodes,
    ):
        """node_state spec should pass when actual matches expected."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "node_state", "node_name": "R1", "expected_state": "running"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "passed"
        assert "running" in result["output"]

    @pytest.mark.asyncio
    async def test_node_state_mismatch(
        self, test_db: Session, runner_lab: models.Lab, runner_nodes,
    ):
        """node_state spec should fail when actual does not match expected."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "node_state", "node_name": "R1", "expected_state": "stopped"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "failed"
        assert "expected=stopped" in result["output"]
        assert "actual=running" in result["output"]

    @pytest.mark.asyncio
    async def test_node_state_not_found(
        self, test_db: Session, runner_lab: models.Lab,
    ):
        """node_state spec should error when node does not exist."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "node_state", "node_name": "GHOST", "expected_state": "running"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "error"
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# TestLinkState
# ---------------------------------------------------------------------------

class TestLinkState:
    """Tests for link_state test type in _run_single_test."""

    @pytest.mark.asyncio
    async def test_link_state_match(
        self, test_db: Session, runner_lab: models.Lab, runner_link,
    ):
        """link_state spec should pass when actual matches expected."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "link_state", "link_name": "R1:eth1-R2:eth1", "expected_state": "up"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_link_state_mismatch(
        self, test_db: Session, runner_lab: models.Lab, runner_link,
    ):
        """link_state spec should fail when actual does not match expected."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "link_state", "link_name": "R1:eth1-R2:eth1", "expected_state": "down"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "failed"
        assert "expected=down" in result["output"]

    @pytest.mark.asyncio
    async def test_link_state_not_found(
        self, test_db: Session, runner_lab: models.Lab,
    ):
        """link_state spec should error when link does not exist."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "link_state", "link_name": "X:eth0-Y:eth0", "expected_state": "up"}
        result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "error"
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# TestPing
# ---------------------------------------------------------------------------

class TestPing:
    """Tests for ping test type in _run_single_test."""

    @pytest.mark.asyncio
    async def test_ping_exit_zero_passes(
        self, test_db: Session, runner_lab: models.Lab, sample_host: models.Host,
    ):
        """Ping with exit_code 0 should pass."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "ping", "source": "R1", "target": "10.0.0.2", "count": 1}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner.agent_client.exec_node_on_agent",
            new_callable=AsyncMock,
            return_value={"output": "1 packets received", "exit_code": 0},
        ):
            result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_ping_nonzero_fails(
        self, test_db: Session, runner_lab: models.Lab, sample_host: models.Host,
    ):
        """Ping with nonzero exit_code should fail."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "ping", "source": "R1", "target": "10.0.0.99"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner.agent_client.exec_node_on_agent",
            new_callable=AsyncMock,
            return_value={"output": "0 packets received", "exit_code": 1},
        ):
            result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_ping_agent_not_resolvable(
        self, test_db: Session, runner_lab: models.Lab,
    ):
        """Ping should error when agent cannot be resolved."""
        from app.tasks.test_runner import _run_single_test

        spec = {"type": "ping", "source": "GHOST", "target": "10.0.0.1"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(None, "GHOST"),
        ):
            result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "error"
        assert "Cannot resolve agent" in result["error"]


# ---------------------------------------------------------------------------
# TestCommand
# ---------------------------------------------------------------------------

class TestCommand:
    """Tests for command test type in _run_single_test."""

    @pytest.mark.asyncio
    async def test_command_expect_pattern_passes(
        self, test_db: Session, runner_lab: models.Lab, sample_host: models.Host,
    ):
        """Command with expect pattern that matches should pass."""
        from app.tasks.test_runner import _run_single_test

        spec = {
            "type": "command",
            "node": "R1",
            "cmd": "show ip route",
            "expect": r"10\.0\.0\.\d+",
        }

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner.agent_client.exec_node_on_agent",
            new_callable=AsyncMock,
            return_value={"output": "10.0.0.1/24 via eth1", "exit_code": 0},
        ):
            result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_command_expect_pattern_fails(
        self, test_db: Session, runner_lab: models.Lab, sample_host: models.Host,
    ):
        """Command with expect pattern that does not match should fail."""
        from app.tasks.test_runner import _run_single_test

        spec = {
            "type": "command",
            "node": "R1",
            "cmd": "show ip route",
            "expect": r"OSPF",
        }

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner.agent_client.exec_node_on_agent",
            new_callable=AsyncMock,
            return_value={"output": "10.0.0.1/24 via eth1", "exit_code": 0},
        ):
            result = await _run_single_test(spec, 0, runner_lab, test_db)

        assert result["status"] == "failed"
        assert "Pattern" in result["error"]


# ---------------------------------------------------------------------------
# TestRunVerificationTests
# ---------------------------------------------------------------------------

class TestRunVerificationTests:
    """Tests for the top-level run_verification_tests function."""

    @pytest.mark.asyncio
    async def test_all_passing_marks_completed(
        self,
        test_db: Session,
        runner_lab: models.Lab,
        runner_job: models.Job,
        runner_nodes,
    ):
        """All passing specs should mark job as COMPLETED."""
        from app.tasks.test_runner import run_verification_tests

        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "running"},
            {"type": "node_state", "node_name": "R2", "expected_state": "running"},
        ]

        with patch(
            "app.tasks.test_runner.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.test_runner.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_test_result = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_verification_tests(runner_job.id, runner_lab.id, specs)

        test_db.refresh(runner_job)
        assert runner_job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_one_failing_marks_failed(
        self,
        test_db: Session,
        runner_lab: models.Lab,
        runner_job: models.Job,
        runner_nodes,
    ):
        """One failing spec should mark the job as FAILED."""
        from app.tasks.test_runner import run_verification_tests

        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "running"},
            {"type": "node_state", "node_name": "R2", "expected_state": "stopped"},
        ]

        with patch(
            "app.tasks.test_runner.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.test_runner.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_test_result = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_verification_tests(runner_job.id, runner_lab.id, specs)

        test_db.refresh(runner_job)
        assert runner_job.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_broadcaster_called_per_result(
        self,
        test_db: Session,
        runner_lab: models.Lab,
        runner_job: models.Job,
        runner_nodes,
    ):
        """Broadcaster should be called once per spec."""
        from app.tasks.test_runner import run_verification_tests

        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "running"},
            {"type": "node_state", "node_name": "R2", "expected_state": "running"},
        ]

        with patch(
            "app.tasks.test_runner.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.test_runner.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_test_result = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_verification_tests(runner_job.id, runner_lab.id, specs)

        assert mock_bc.publish_test_result.call_count == len(specs)

    @pytest.mark.asyncio
    async def test_unknown_test_type_errors(
        self,
        test_db: Session,
        runner_lab: models.Lab,
        runner_job: models.Job,
    ):
        """Unknown test type should produce an error result."""
        from app.tasks.test_runner import run_verification_tests

        specs = [
            {"type": "invalid_type", "name": "bad_test"},
        ]

        with patch(
            "app.tasks.test_runner.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.test_runner.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_test_result = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_verification_tests(runner_job.id, runner_lab.id, specs)

        test_db.refresh(runner_job)
        assert runner_job.status == JobStatus.FAILED

        # Verify the broadcast included the error result
        call_kwargs = mock_bc.publish_test_result.call_args_list[0].kwargs
        assert call_kwargs["result"]["status"] == "error"
