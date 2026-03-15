"""Tests for app/tasks/scenario_executor.py — Scenario engine executor."""
from __future__ import annotations

from contextlib import contextmanager
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
def scenario_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a running lab for scenario tests."""
    lab = models.Lab(
        name="Scenario Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/scenario-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def scenario_job(
    test_db: Session,
    scenario_lab: models.Lab,
    test_user: models.User,
    tmp_path,
) -> models.Job:
    """Create a queued scenario job."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=scenario_lab.id,
        user_id=test_user.id,
        action="scenario",
        status="queued",
        log_path=str(tmp_path / "scenario.json"),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture()
def scenario_nodes(
    test_db: Session, scenario_lab: models.Lab
) -> list[models.NodeState]:
    """Create node states attached to the scenario lab."""
    nodes = []
    for name in ("R1", "R2"):
        ns = models.NodeState(
            lab_id=scenario_lab.id,
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
def scenario_link(
    test_db: Session, scenario_lab: models.Lab, sample_host: models.Host,
) -> models.LinkState:
    """Create a link state for scenario carrier tests."""
    ls = models.LinkState(
        id=str(uuid4()),
        lab_id=scenario_lab.id,
        link_name="R1:eth1 <-> R2:eth1",
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        desired_state="up",
        actual_state="up",
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


# ---------------------------------------------------------------------------
# TestStepWait
# ---------------------------------------------------------------------------

class TestStepWait:
    """Tests for the _step_wait handler."""

    @pytest.mark.asyncio
    async def test_returns_passed(self, scenario_lab: models.Lab, test_db: Session):
        """Wait step should always return passed."""
        from app.tasks.scenario_executor import _step_wait

        step = {"type": "wait", "seconds": 0}
        result = await _step_wait(step, scenario_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_uses_step_seconds(self, scenario_lab: models.Lab, test_db: Session):
        """Wait step should report the configured wait duration."""
        from app.tasks.scenario_executor import _step_wait

        step = {"type": "wait", "seconds": 7}
        result = await _step_wait(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert "7" in result["output"]

    @pytest.mark.asyncio
    async def test_defaults_to_five_seconds(self, scenario_lab: models.Lab, test_db: Session):
        """Wait step without seconds should default to 5."""
        from app.tasks.scenario_executor import _step_wait

        step = {"type": "wait"}
        result = await _step_wait(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert "5" in result["output"]


# ---------------------------------------------------------------------------
# TestStepExec
# ---------------------------------------------------------------------------

class TestStepExec:
    """Tests for the _step_exec handler."""

    @pytest.mark.asyncio
    async def test_exit_code_zero_passes(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """Exec step with exit_code 0 should pass."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "echo hello"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner._resolve_node_exec_method",
            return_value=("docker_exec", None),
        ), patch(
            "app.tasks.test_runner._exec_on_node",
            new_callable=AsyncMock,
            return_value={"output": "hello", "exit_code": 0},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert result["output"] == "hello"

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_fails(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """Exec step with nonzero exit_code should fail."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "false"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner._resolve_node_exec_method",
            return_value=("docker_exec", None),
        ), patch(
            "app.tasks.test_runner._exec_on_node",
            new_callable=AsyncMock,
            return_value={"output": "", "exit_code": 1},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "exit_code=1" in result["error"]

    @pytest.mark.asyncio
    async def test_expect_pattern_match(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """Exec step with expect pattern that matches output should pass."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "show ver", "expect": r"version\s+\d+"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner._resolve_node_exec_method",
            return_value=("docker_exec", None),
        ), patch(
            "app.tasks.test_runner._exec_on_node",
            new_callable=AsyncMock,
            return_value={"output": "version 42", "exit_code": 0},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_expect_pattern_no_match(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """Exec step with expect pattern that does not match should fail."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "show ver", "expect": r"MISSING"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner._resolve_node_exec_method",
            return_value=("docker_exec", None),
        ), patch(
            "app.tasks.test_runner._exec_on_node",
            new_callable=AsyncMock,
            return_value={"output": "version 42", "exit_code": 0},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "MISSING" in result["error"]

    @pytest.mark.asyncio
    async def test_no_placement_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """Exec step should error when agent cannot be resolved for the node."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "GHOST", "cmd": "echo x"}

        with patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(None, "GHOST"),
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "Cannot resolve agent" in result["error"]


# ---------------------------------------------------------------------------
# TestStepVerify
# ---------------------------------------------------------------------------

class TestStepVerify:
    """Tests for the _step_verify handler."""

    @pytest.mark.asyncio
    async def test_delegates_to_test_runner(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """Verify step should call _run_single_test for each spec and return passed."""
        from app.tasks.scenario_executor import _step_verify

        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "running"},
        ]
        step = {"type": "verify", "specs": specs}

        result = await _step_verify(step, scenario_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_fails_fast_on_first_failure(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """Verify step should stop at first failing spec."""
        from app.tasks.scenario_executor import _step_verify

        # First spec will fail (expected stopped but actual is running)
        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "stopped"},
            {"type": "node_state", "node_name": "R2", "expected_state": "running"},
        ]
        step = {"type": "verify", "specs": specs}

        result = await _step_verify(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        # Only one result line — second spec was never executed
        assert result["output"].count("\n") == 0

    @pytest.mark.asyncio
    async def test_empty_specs_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """Verify step with no specs should return error."""
        from app.tasks.scenario_executor import _step_verify

        step = {"type": "verify", "specs": []}
        result = await _step_verify(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "no specs" in result["error"]


# ---------------------------------------------------------------------------
# TestStepLinkCarrier
# ---------------------------------------------------------------------------

class TestStepLinkCarrier:
    """Tests for the _step_link_down / _step_link_up handlers."""

    @pytest.mark.asyncio
    async def test_link_down_sets_carrier_off(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_link: models.LinkState,
        sample_host: models.Host,
    ):
        """link_down step should call carrier off on both endpoints."""
        from app.tasks.scenario_executor import _step_link_down

        step = {"type": "link_down", "link": "R1:eth1 <-> R2:eth1"}

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch(
            "app.tasks.scenario_executor.agent_client.get_http_client",
            return_value=mock_client,
        ), patch(
            "app.utils.nodes.get_node_placement_mapping",
            return_value=({}, {}),
        ):
            result = await _step_link_down(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert "carrier off" in result["output"]

    @pytest.mark.asyncio
    async def test_link_not_found_returns_error(
        self, test_db: Session, scenario_lab: models.Lab,
    ):
        """link_down for a non-existent link should return error."""
        from app.tasks.scenario_executor import _step_link_down

        step = {"type": "link_down", "link": "FAKE:eth1 <-> GHOST:eth1"}

        result = await _step_link_down(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "Link not found" in result["error"]


# ---------------------------------------------------------------------------
# TestRunScenario
# ---------------------------------------------------------------------------

class TestRunScenario:
    """Tests for the top-level run_scenario function."""

    @pytest.mark.asyncio
    async def test_completes_job_on_all_passing(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
    ):
        """Scenario with all passing steps should set job to COMPLETED."""
        from app.tasks.scenario_executor import run_scenario

        steps = [
            {"type": "wait", "name": "short_wait", "seconds": 0},
            {"type": "wait", "name": "another_wait", "seconds": 0},
        ]

        with patch(
            "app.tasks.scenario_executor.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.scenario_executor.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_scenario_step = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_scenario(
                scenario_job.id,
                scenario_lab.id,
                steps,
                "test_scenario",
            )

        test_db.refresh(scenario_job)
        assert scenario_job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_stops_on_first_failure(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
        sample_host: models.Host,
    ):
        """Scenario should stop executing after the first failing step."""
        from app.tasks.scenario_executor import run_scenario

        steps = [
            {"type": "exec", "name": "failing_cmd", "node": "R1", "cmd": "false"},
            {"type": "wait", "name": "should_not_run", "seconds": 0},
        ]

        with patch(
            "app.tasks.scenario_executor.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.scenario_executor.get_broadcaster",
        ) as mock_bc_factory, patch(
            "app.tasks.test_runner._resolve_agent_for_node",
            new_callable=AsyncMock,
            return_value=(sample_host, "R1"),
        ), patch(
            "app.tasks.test_runner._exec_on_node",
            new_callable=AsyncMock,
            return_value={"output": "", "exit_code": 1},
        ):
            mock_bc = MagicMock()
            mock_bc.publish_scenario_step = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_scenario(
                scenario_job.id,
                scenario_lab.id,
                steps,
                "test_scenario",
            )

        test_db.refresh(scenario_job)
        assert scenario_job.status == JobStatus.FAILED

        # Only 3 broadcast calls: step 0 running, step 0 completed, overall completion
        # (step 1 should NOT have been started)
        calls = mock_bc.publish_scenario_step.call_args_list
        step_indices = [c.kwargs.get("step_index") for c in calls]
        assert 1 not in [i for i in step_indices if i >= 0], \
            "Step 1 should not have been executed"

    @pytest.mark.asyncio
    async def test_unknown_step_type_errors(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
    ):
        """Unknown step type should produce an error result and fail the job."""
        from app.tasks.scenario_executor import run_scenario

        steps = [
            {"type": "bogus_step", "name": "bad"},
        ]

        with patch(
            "app.tasks.scenario_executor.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.scenario_executor.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_scenario_step = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_scenario(
                scenario_job.id,
                scenario_lab.id,
                steps,
                "test_scenario",
            )

        test_db.refresh(scenario_job)
        assert scenario_job.status == JobStatus.FAILED
