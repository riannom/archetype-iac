"""Tests for app/tasks/scenario_executor.py — Round 12 deep-path coverage.

Targets: _step_exec edge cases, _set_node_desired_state timeout/polling,
_set_link_carrier partial failures, run_scenario exception handling,
_execute_step dispatch, _broadcast_step error suppression, and log writing.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
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
        name="Scenario Lab R12",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/scenario-lab-r12",
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
    """Create a queued scenario job with a log path."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=scenario_lab.id,
        user_id=test_user.id,
        action="scenario",
        status="queued",
        log_path=str(tmp_path / "scenario-r12.json"),
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


@pytest.fixture()
def scenario_job_no_log(
    test_db: Session,
    scenario_lab: models.Lab,
    test_user: models.User,
) -> models.Job:
    """Create a queued scenario job without a log path."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=scenario_lab.id,
        user_id=test_user.id,
        action="scenario",
        status="queued",
        log_path=None,
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
# TestExecuteStepDispatch
# ---------------------------------------------------------------------------

class TestExecuteStepDispatch:
    """Tests for the _execute_step dispatch function."""

    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """An unregistered step type should return an error result."""
        from app.tasks.scenario_executor import _execute_step

        step = {"type": "nonexistent_handler"}
        result = await _execute_step(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "Unknown step type" in result["error"]
        assert "nonexistent_handler" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_type_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """A step with an empty type string should return error."""
        from app.tasks.scenario_executor import _execute_step

        step = {"type": ""}
        result = await _execute_step(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "Unknown step type" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_type_key_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """A step without a 'type' key defaults to empty string and errors."""
        from app.tasks.scenario_executor import _execute_step

        step = {"name": "no-type"}
        result = await _execute_step(step, scenario_lab, test_db)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# TestStepExecDeepPaths
# ---------------------------------------------------------------------------

class TestStepExecDeepPaths:
    """Deep-path tests for _step_exec: regex edge cases, missing fields."""

    @pytest.mark.asyncio
    async def test_expect_regex_special_chars(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """Expect pattern with regex special chars should be applied as-is."""
        from app.tasks.scenario_executor import _step_run_command

        step = {
            "type": "exec", "node": "R1", "cmd": "show ip",
            "expect": r"192\.168\.1\.\d+",
        }

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
            return_value={"output": "addr: 192.168.1.42/24", "exit_code": 0},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_expect_overrides_exit_code(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """When expect is set and matches, exit_code is ignored (even nonzero)."""
        from app.tasks.scenario_executor import _step_run_command

        step = {
            "type": "exec", "node": "R1", "cmd": "show ver",
            "expect": r"Version",
        }

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
            return_value={"output": "Version 4.28", "exit_code": 1},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        # expect match takes precedence over exit_code
        assert result["status"] == "passed"

    @pytest.mark.asyncio
    async def test_missing_output_key_defaults_empty(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """If agent response lacks 'output', default to empty string."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "echo x"}

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
            return_value={"exit_code": 0},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert result["output"] == ""

    @pytest.mark.asyncio
    async def test_missing_exit_code_defaults_negative(
        self, scenario_lab: models.Lab, test_db: Session, sample_host: models.Host,
    ):
        """If agent response lacks 'exit_code', default to -1 (fails)."""
        from app.tasks.scenario_executor import _step_run_command

        step = {"type": "exec", "node": "R1", "cmd": "echo x"}

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
            return_value={"output": "something"},
        ):
            result = await _step_run_command(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "exit_code=-1" in result["error"]


# ---------------------------------------------------------------------------
# TestSetNodeDesiredStateTimeout
# ---------------------------------------------------------------------------

class TestSetNodeDesiredStateTimeout:
    """Tests for _set_node_desired_state timeout and polling logic."""

    @pytest.mark.asyncio
    async def test_node_stop_timeout(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """node_stop should return failed when node never reaches stopped state."""
        from app.tasks.scenario_executor import _step_node_stop

        # Node stays running — never transitions to stopped
        step = {"type": "node_stop", "node": "R1", "timeout": 0}

        with patch(
            "app.tasks.state_enforcement.enforce_node_state",
            new_callable=AsyncMock,
        ):
            result = await _step_node_stop(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "Timeout" in result["output"]
        assert "wanted=stopped" in result["output"]

    @pytest.mark.asyncio
    async def test_node_start_immediate_success(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """node_start should pass when node is already running.

        With timeout=1, the polling loop runs and finds actual_state==running
        on the first refresh, returning success immediately.
        """
        from app.tasks.scenario_executor import _step_node_start

        # Node is already running (from scenario_nodes fixture)
        step = {"type": "node_start", "node": "R1", "timeout": 1}

        with patch(
            "app.tasks.state_enforcement.enforce_node_state",
            new_callable=AsyncMock,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _step_node_start(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        assert "R1 is now running" in result["output"]

    @pytest.mark.asyncio
    async def test_node_not_found_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """node_stop on a nonexistent node should return error."""
        from app.tasks.scenario_executor import _step_node_stop

        step = {"type": "node_stop", "node": "GHOST_NODE"}

        result = await _step_node_stop(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "Node not found" in result["error"]
        assert "GHOST_NODE" in result["error"]

    @pytest.mark.asyncio
    async def test_enforcement_exception_is_warning_not_failure(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """If enforce_node_state raises, the step should still poll (not immediately error).

        With timeout=1, polling enters the loop and finds actual_state==running.
        """
        from app.tasks.scenario_executor import _step_node_start

        step = {"type": "node_start", "node": "R1", "timeout": 1}

        with patch(
            "app.tasks.state_enforcement.enforce_node_state",
            new_callable=AsyncMock,
            side_effect=RuntimeError("enforcement broke"),
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            # Node is already running so it should pass even though enforcement failed
            result = await _step_node_start(step, scenario_lab, test_db)

        # Already running => passed (enforcement failure is just a warning)
        assert result["status"] == "passed"


# ---------------------------------------------------------------------------
# TestSetLinkCarrierPartialFailure
# ---------------------------------------------------------------------------

class TestSetLinkCarrierPartialFailure:
    """Tests for _set_link_carrier when one endpoint fails."""

    @pytest.mark.asyncio
    async def test_one_endpoint_http_error(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_link: models.LinkState,
        sample_host: models.Host,
    ):
        """If one endpoint returns non-200, the step should fail."""
        from app.tasks.scenario_executor import _step_link_up

        step = {"type": "link_up", "link": "R1:eth1 <-> R2:eth1"}

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200 if call_count == 1 else 500
            return resp

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)

        with patch(
            "app.tasks.scenario_executor.agent_client.get_http_client",
            return_value=mock_client,
        ), patch(
            "app.utils.nodes.get_node_placement_mapping",
            return_value=({}, {}),
        ):
            result = await _step_link_up(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "HTTP 500" in result["output"]

    @pytest.mark.asyncio
    async def test_endpoint_exception_captured(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_link: models.LinkState,
        sample_host: models.Host,
    ):
        """If an endpoint raises an exception, it should be captured in output."""
        from app.tasks.scenario_executor import _step_link_down

        step = {"type": "link_down", "link": "R1:eth1 <-> R2:eth1"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("Agent unreachable"))

        with patch(
            "app.tasks.scenario_executor.agent_client.get_http_client",
            return_value=mock_client,
        ), patch(
            "app.utils.nodes.get_node_placement_mapping",
            return_value=({}, {}),
        ):
            result = await _step_link_down(step, scenario_lab, test_db)

        assert result["status"] == "failed"
        assert "Agent unreachable" in result["output"]

    @pytest.mark.asyncio
    async def test_link_fuzzy_match_by_node_names(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_link: models.LinkState,
        sample_host: models.Host,
    ):
        """Link can be matched by node names alone (without interface specifiers)."""
        from app.tasks.scenario_executor import _step_link_down

        # Use node names only (no interface), which triggers the fuzzy match path
        step = {"type": "link_down", "link": "R1 <-> R2"}

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


# ---------------------------------------------------------------------------
# TestRunScenarioDeepPaths
# ---------------------------------------------------------------------------

class TestRunScenarioDeepPaths:
    """Deep-path tests for run_scenario: log writing, missing job/lab, exception in step."""

    @pytest.mark.asyncio
    async def test_missing_job_returns_early(self, test_db: Session, scenario_lab: models.Lab):
        """If job_id does not exist, run_scenario should return without crashing."""
        from app.tasks.scenario_executor import run_scenario

        with patch(
            "app.tasks.scenario_executor.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.scenario_executor.get_broadcaster",
        ) as mock_bc_factory:
            mock_bc = MagicMock()
            mock_bc.publish_scenario_step = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            # Should not raise
            await run_scenario("nonexistent-job-id", scenario_lab.id, [], "test")

    @pytest.mark.asyncio
    async def test_missing_lab_fails_job(
        self, test_db: Session, scenario_job: models.Job, test_user: models.User,
    ):
        """If lab does not exist, the job should be marked FAILED."""
        from app.tasks.scenario_executor import run_scenario

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
                "nonexistent-lab-id",
                [{"type": "wait", "seconds": 0}],
                "test",
            )

        test_db.refresh(scenario_job)
        assert scenario_job.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_step_exception_captured_and_fails_job(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
    ):
        """If a step handler raises an unexpected exception, it should be captured."""
        from app.tasks.scenario_executor import run_scenario

        async def _exploding_handler(step, lab, db):
            raise ValueError("kaboom")

        with patch(
            "app.tasks.scenario_executor.get_session",
            _fake_get_session(test_db),
        ), patch(
            "app.tasks.scenario_executor.get_broadcaster",
        ) as mock_bc_factory, patch(
            "app.tasks.scenario_executor._STEP_HANDLERS",
            {"boom": _exploding_handler},
        ):
            mock_bc = MagicMock()
            mock_bc.publish_scenario_step = AsyncMock()
            mock_bc_factory.return_value = mock_bc

            await run_scenario(
                scenario_job.id,
                scenario_lab.id,
                [{"type": "boom", "name": "exploder"}],
                "test",
            )

        test_db.refresh(scenario_job)
        assert scenario_job.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_results_written_to_log_path(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
        tmp_path,
    ):
        """Completed scenario should write structured JSON to job.log_path."""
        from app.tasks.scenario_executor import run_scenario

        steps = [
            {"type": "wait", "name": "tiny_wait", "seconds": 0},
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
                "my_scenario",
            )

        log_path = Path(scenario_job.log_path)
        assert log_path.exists()
        data = json.loads(log_path.read_text())
        assert data["scenario_name"] == "my_scenario"
        assert data["status"] == "passed"
        assert data["total_steps"] == 1
        assert data["completed_steps"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["step_name"] == "tiny_wait"

    @pytest.mark.asyncio
    async def test_no_log_path_does_not_crash(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job_no_log: models.Job,
    ):
        """Scenario with log_path=None should complete without error."""
        from app.tasks.scenario_executor import run_scenario

        steps = [{"type": "wait", "name": "w", "seconds": 0}]

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
                scenario_job_no_log.id,
                scenario_lab.id,
                steps,
                "test",
            )

        test_db.refresh(scenario_job_no_log)
        assert scenario_job_no_log.status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# TestBroadcastStepErrorSuppression
# ---------------------------------------------------------------------------

class TestBroadcastStepErrorSuppression:
    """Tests for _broadcast_step exception suppression."""

    @pytest.mark.asyncio
    async def test_broadcast_exception_does_not_propagate(self):
        """If broadcaster.publish_scenario_step raises, _broadcast_step swallows it."""
        from app.tasks.scenario_executor import _broadcast_step

        mock_bc = MagicMock()
        mock_bc.publish_scenario_step = AsyncMock(
            side_effect=RuntimeError("Redis down"),
        )

        # Should not raise
        await _broadcast_step(
            mock_bc, "lab-1", "job-1", 0, "test_step", "wait",
            "running", 1,
        )


# ---------------------------------------------------------------------------
# TestStepVerifyDeepPaths
# ---------------------------------------------------------------------------

class TestStepVerifyDeepPaths:
    """Deep-path tests for _step_verify."""

    @pytest.mark.asyncio
    async def test_missing_specs_key_returns_error(
        self, scenario_lab: models.Lab, test_db: Session,
    ):
        """verify step with no 'specs' key should return error."""
        from app.tasks.scenario_executor import _step_verify

        step = {"type": "verify"}
        result = await _step_verify(step, scenario_lab, test_db)

        assert result["status"] == "error"
        assert "no specs" in result["error"]

    @pytest.mark.asyncio
    async def test_multiple_passing_specs(
        self, scenario_lab: models.Lab, test_db: Session, scenario_nodes,
    ):
        """verify step with multiple passing specs should return passed with all outputs."""
        from app.tasks.scenario_executor import _step_verify

        specs = [
            {"type": "node_state", "node_name": "R1", "expected_state": "running"},
            {"type": "node_state", "node_name": "R2", "expected_state": "running"},
        ]
        step = {"type": "verify", "specs": specs}

        result = await _step_verify(step, scenario_lab, test_db)

        assert result["status"] == "passed"
        # Both spec results should appear in the output (format: "node_state_N: passed")
        lines = result["output"].strip().split("\n")
        assert len(lines) == 2
        assert all("passed" in line for line in lines)


# ---------------------------------------------------------------------------
# TestRunScenarioDurationTracking
# ---------------------------------------------------------------------------

class TestRunScenarioDurationTracking:
    """Tests for duration_ms tracking in run_scenario."""

    @pytest.mark.asyncio
    async def test_step_results_include_duration(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
    ):
        """Each step result in the log should include duration_ms."""
        from app.tasks.scenario_executor import run_scenario

        steps = [
            {"type": "wait", "name": "step_a", "seconds": 0},
            {"type": "wait", "name": "step_b", "seconds": 0},
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
                "duration_test",
            )

        log_data = json.loads(Path(scenario_job.log_path).read_text())
        for r in log_data["results"]:
            assert "duration_ms" in r
            assert isinstance(r["duration_ms"], float)
            assert r["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_step_metadata_populated(
        self,
        test_db: Session,
        scenario_lab: models.Lab,
        scenario_job: models.Job,
    ):
        """Step results should carry step_index, step_name, step_type metadata."""
        from app.tasks.scenario_executor import run_scenario

        steps = [{"type": "wait", "name": "meta_step", "seconds": 0}]

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
                "meta_test",
            )

        log_data = json.loads(Path(scenario_job.log_path).read_text())
        result = log_data["results"][0]
        assert result["step_index"] == 0
        assert result["step_name"] == "meta_step"
        assert result["step_type"] == "wait"
