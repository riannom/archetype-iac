"""Lab verification test runner.

Executes test specs against a deployed lab, broadcasting results via WS.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from app import agent_client, models
from app.db import get_session
from app.services.broadcaster import get_broadcaster
from app.state import JobStatus

logger = logging.getLogger(__name__)


async def _run_single_test(
    spec: dict,
    index: int,
    lab: models.Lab,
    database,
) -> dict:
    """Execute one test spec and return a result dict."""
    spec_type = spec.get("type", "")
    spec_name = spec.get("name") or f"{spec_type}_{index}"
    start = time.monotonic()

    try:
        if spec_type == "node_state":
            node_name = spec.get("node_name") or spec.get("node")
            expected = spec.get("expected_state", "running")
            ns = (
                database.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab.id,
                    models.NodeState.node_name == node_name,
                )
                .first()
            )
            if not ns:
                return _result(index, spec_name, "error", start, error=f"Node '{node_name}' not found")
            actual = ns.actual_state
            if actual == expected:
                return _result(index, spec_name, "passed", start, output=f"actual_state={actual}")
            return _result(index, spec_name, "failed", start, output=f"expected={expected}, actual={actual}")

        elif spec_type == "link_state":
            link_name = spec.get("link_name")
            expected = spec.get("expected_state", "up")
            ls = (
                database.query(models.LinkState)
                .filter(
                    models.LinkState.lab_id == lab.id,
                    models.LinkState.link_name == link_name,
                )
                .first()
            )
            if not ls:
                return _result(index, spec_name, "error", start, error=f"Link '{link_name}' not found")
            actual = ls.actual_state
            if actual == expected:
                return _result(index, spec_name, "passed", start, output=f"actual_state={actual}")
            return _result(index, spec_name, "failed", start, output=f"expected={expected}, actual={actual}")

        elif spec_type == "ping":
            source = spec.get("source")
            target = spec.get("target")
            count = spec.get("count", 3)
            agent, node_name = await _resolve_agent_for_node(database, lab, source)
            if not agent:
                return _result(index, spec_name, "error", start, error=f"Cannot resolve agent for node '{source}'")
            cmd = f"ping -c {count} -W 2 {target}"
            resp = await agent_client.exec_node_on_agent(agent, lab.id, node_name, cmd)
            output = resp.get("output", "")
            exit_code = resp.get("exit_code", -1)
            if exit_code == 0:
                return _result(index, spec_name, "passed", start, output=output)
            return _result(index, spec_name, "failed", start, output=output)

        elif spec_type == "command":
            node = spec.get("node")
            cmd = spec.get("cmd", "")
            expect_pattern = spec.get("expect")
            agent, node_name = await _resolve_agent_for_node(database, lab, node)
            if not agent:
                return _result(index, spec_name, "error", start, error=f"Cannot resolve agent for node '{node}'")
            resp = await agent_client.exec_node_on_agent(agent, lab.id, node_name, cmd)
            output = resp.get("output", "")
            exit_code = resp.get("exit_code", -1)
            if expect_pattern:
                if re.search(expect_pattern, output):
                    return _result(index, spec_name, "passed", start, output=output)
                return _result(index, spec_name, "failed", start, output=output, error=f"Pattern '{expect_pattern}' not found")
            if exit_code == 0:
                return _result(index, spec_name, "passed", start, output=output)
            return _result(index, spec_name, "failed", start, output=output)

        else:
            return _result(index, spec_name, "error", start, error=f"Unknown test type: {spec_type}")

    except Exception as e:
        return _result(index, spec_name, "error", start, error=str(e))


def _result(index: int, name: str, status: str, start: float, output: str | None = None, error: str | None = None) -> dict:
    return {
        "spec_index": index,
        "spec_name": name,
        "status": status,
        "duration_ms": round((time.monotonic() - start) * 1000, 1),
        "output": output,
        "error": error,
    }


async def _resolve_agent_for_node(database, lab: models.Lab, node_name: str):
    """Resolve the agent Host object and container name for a node."""
    from app.utils.nodes import get_node_placement_mapping

    placement_by_node, _ = get_node_placement_mapping(database, lab.id, lab.agent_id)
    host_id = placement_by_node.get(node_name) or lab.agent_id
    if not host_id:
        return None, node_name

    agent = database.get(models.Host, host_id)
    return agent, node_name


async def run_verification_tests(job_id: str, lab_id: str, specs: list[dict]) -> None:
    """Async entry point for running verification tests.

    Iterates specs, runs each, broadcasts per-test results via WS,
    and writes structured JSON to job.log_path on completion.
    """
    broadcaster = get_broadcaster()
    results: list[dict] = []
    passed = 0
    failed = 0
    errors = 0

    with get_session() as database:
        job = database.get(models.Job, job_id)
        if not job:
            logger.error(f"Test runner: job {job_id} not found")
            return
        job.status = JobStatus.RUNNING
        database.commit()

        lab = database.get(models.Lab, lab_id)
        if not lab:
            job.status = JobStatus.FAILED
            database.commit()
            return

        for i, spec in enumerate(specs):
            result = await _run_single_test(spec, i, lab, database)
            results.append(result)

            if result["status"] == "passed":
                passed += 1
            elif result["status"] == "failed":
                failed += 1
            else:
                errors += 1

            # Broadcast per-test result
            try:
                await broadcaster.publish_test_result(
                    lab_id=lab_id,
                    job_id=job_id,
                    result=result,
                    summary={"total": len(specs), "passed": passed, "failed": failed, "errors": errors},
                )
            except Exception as e:
                logger.warning(f"Failed to broadcast test result: {e}")

        # Write structured results to job log
        run_result = {
            "job_id": job_id,
            "total": len(specs),
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "results": results,
        }

        if job.log_path:
            try:
                Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(job.log_path).write_text(json.dumps(run_result, indent=2))
            except Exception as e:
                logger.warning(f"Failed to write test results to {job.log_path}: {e}")

        job.status = JobStatus.COMPLETED if failed == 0 and errors == 0 else JobStatus.FAILED
        database.commit()
