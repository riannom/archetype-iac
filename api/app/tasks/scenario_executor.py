"""Scenario engine executor — runs YAML-defined scenario steps sequentially."""
from __future__ import annotations

import asyncio
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

# Step type dispatch table
_STEP_HANDLERS: dict[str, ...] = {}  # populated below


async def run_scenario(
    job_id: str,
    lab_id: str,
    steps: list[dict],
    scenario_name: str,
) -> None:
    """Async entry point — execute scenario steps sequentially with WS broadcasting."""
    broadcaster = get_broadcaster()
    results: list[dict] = []
    overall_status = "passed"

    with get_session() as database:
        job = database.get(models.Job, job_id)
        if not job:
            logger.error(f"Scenario executor: job {job_id} not found")
            return
        job.status = JobStatus.RUNNING
        database.commit()

        lab = database.get(models.Lab, lab_id)
        if not lab:
            job.status = JobStatus.FAILED
            database.commit()
            return

        total = len(steps)
        for i, step in enumerate(steps):
            step_type = step.get("type", "unknown")
            step_name = step.get("name", f"{step_type}_{i}")

            # Broadcast: step starting
            await _broadcast_step(broadcaster, lab_id, job_id, i, step_name, step_type, "running", total, step_data=step)

            start = time.monotonic()
            try:
                result = await _execute_step(step, lab, database)
            except Exception as e:
                logger.exception(f"Scenario step {i} ({step_name}) failed with exception")
                result = {"status": "error", "output": None, "error": str(e)}

            duration_ms = round((time.monotonic() - start) * 1000, 1)
            result["step_index"] = i
            result["step_name"] = step_name
            result["step_type"] = step_type
            result["duration_ms"] = duration_ms
            results.append(result)

            # Broadcast: step completed
            await _broadcast_step(
                broadcaster, lab_id, job_id, i, step_name, step_type,
                result["status"], total, duration_ms=duration_ms,
                output=result.get("output"), error=result.get("error"),
                step_data=step,
            )

            # Fail-fast: stop on first failure
            if result["status"] in ("failed", "error"):
                overall_status = "failed"
                break

        # Broadcast overall completion (step_index=-1 signals completion)
        await _broadcast_step(
            broadcaster, lab_id, job_id, -1, scenario_name, "completion",
            overall_status, total,
        )

        # Write structured results to job log
        run_result = {
            "job_id": job_id,
            "scenario_name": scenario_name,
            "total_steps": total,
            "completed_steps": len(results),
            "status": overall_status,
            "results": results,
        }
        if job.log_path:
            try:
                Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(job.log_path).write_text(json.dumps(run_result, indent=2))
            except Exception as e:
                logger.warning(f"Failed to write scenario results to {job.log_path}: {e}")

        job.status = JobStatus.COMPLETED if overall_status == "passed" else JobStatus.FAILED
        database.commit()


async def _broadcast_step(
    broadcaster, lab_id, job_id, step_index, step_name, step_type,
    status, total_steps, duration_ms=None, output=None, error=None,
    step_data=None,
):
    """Publish a scenario_step WS message."""
    try:
        await broadcaster.publish_scenario_step(
            lab_id=lab_id,
            job_id=job_id,
            step_index=step_index,
            step_name=step_name,
            step_type=step_type,
            status=status,
            total_steps=total_steps,
            duration_ms=duration_ms,
            output=output,
            error=error,
            step_data=step_data,
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast scenario step: {e}")


async def _execute_step(step: dict, lab: models.Lab, database) -> dict:
    """Dispatch a single step to the appropriate handler."""
    step_type = step.get("type", "")
    handler = _STEP_HANDLERS.get(step_type)
    if not handler:
        return {"status": "error", "output": None, "error": f"Unknown step type: {step_type}"}
    return await handler(step, lab, database)


# --- Step handlers ---

async def _step_verify(step: dict, lab: models.Lab, database) -> dict:
    """Run verification specs (reuses test_runner._run_single_test)."""
    from app.tasks.test_runner import _run_single_test

    specs = step.get("specs", [])
    if not specs:
        return {"status": "error", "output": None, "error": "verify step has no specs"}

    outputs = []
    for i, spec in enumerate(specs):
        result = await _run_single_test(spec, i, lab, database)
        outputs.append(f"{result['spec_name']}: {result['status']}")
        if result["status"] in ("failed", "error"):
            return {
                "status": result["status"],
                "output": "\n".join(outputs),
                "error": result.get("error"),
            }

    return {"status": "passed", "output": "\n".join(outputs), "error": None}


async def _step_link_down(step: dict, lab: models.Lab, database) -> dict:
    """Set carrier off on both endpoints of a link."""
    return await _set_link_carrier(step, lab, database, "off")


async def _step_link_up(step: dict, lab: models.Lab, database) -> dict:
    """Set carrier on on both endpoints of a link."""
    return await _set_link_carrier(step, lab, database, "on")


async def _set_link_carrier(step: dict, lab: models.Lab, database, carrier_state: str) -> dict:
    """Shared implementation for link_down / link_up steps."""
    link_spec = step.get("link", "")
    # Find the LinkState record by matching link_name
    all_links = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab.id)
        .all()
    )
    # Match against link_name (format: "node1:iface1 <-> node2:iface2")
    target_ls = None
    for link_state in all_links:
        if link_state.link_name == link_spec:
            target_ls = link_state
            break
    # Also try matching just the node names for convenience
    if not target_ls and " <-> " in link_spec:
        parts = link_spec.split(" <-> ")
        if len(parts) == 2:
            for link_state in all_links:
                ln = link_state.link_name
                if all(p.strip().split(":")[0] in ln for p in parts):
                    target_ls = link_state
                    break

    if not target_ls:
        return {"status": "error", "output": None, "error": f"Link not found: {link_spec}"}

    # Resolve agents for both endpoints
    from app.utils.nodes import get_node_placement_mapping
    placement_by_node, _ = get_node_placement_mapping(database, lab.id, lab.agent_id)

    results = []
    for endpoint in ("source", "target"):
        node = target_ls.source_node if endpoint == "source" else target_ls.target_node
        interface = target_ls.source_interface if endpoint == "source" else target_ls.target_interface
        host_id = (target_ls.source_host_id if endpoint == "source" else target_ls.target_host_id) or placement_by_node.get(node) or lab.agent_id

        agent = database.get(models.Host, host_id) if host_id else None
        if not agent:
            results.append(f"{endpoint}: agent not found for {node}")
            continue

        try:
            url = f"http://{agent.address}/labs/{lab.id}/interfaces/{node}/{interface}/carrier"
            client = agent_client.get_http_client()
            response = await client.post(url, json={"state": carrier_state}, timeout=10.0)
            if response.status_code == 200:
                # Update DB carrier state
                if endpoint == "source":
                    target_ls.source_carrier_state = carrier_state
                else:
                    target_ls.target_carrier_state = carrier_state
                results.append(f"{endpoint} ({node}:{interface}): carrier {carrier_state}")
            else:
                results.append(f"{endpoint} ({node}:{interface}): HTTP {response.status_code}")
        except Exception as e:
            results.append(f"{endpoint} ({node}:{interface}): {e}")

    database.commit()

    # Brief wait for state to propagate
    await asyncio.sleep(2)

    output = "\n".join(results)
    all_ok = all("carrier " + carrier_state in r for r in results)
    return {"status": "passed" if all_ok else "failed", "output": output, "error": None}


async def _step_node_stop(step: dict, lab: models.Lab, database) -> dict:
    """Stop a node by setting desired_state=stopped and waiting."""
    return await _set_node_desired_state(step, lab, database, "stopped", "stopped", step.get("timeout", 60))


async def _step_node_start(step: dict, lab: models.Lab, database) -> dict:
    """Start a node by setting desired_state=running and waiting."""
    return await _set_node_desired_state(step, lab, database, "running", "running", step.get("timeout", 120))


async def _set_node_desired_state(
    step: dict, lab: models.Lab, database,
    desired: str, target_actual: str, timeout: int,
) -> dict:
    """Set desired_state on a node and poll until actual_state matches or timeout."""
    node_name = step.get("node", "")
    ns = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab.id,
            models.NodeState.node_name == node_name,
        )
        .first()
    )
    if not ns:
        return {"status": "error", "output": None, "error": f"Node not found: {node_name}"}

    ns.desired_state = desired
    database.commit()

    # Trigger state sync via the existing enforcement mechanism
    try:
        from app.tasks.state_enforcement import enforce_node_state
        await enforce_node_state(database, lab, ns)
    except Exception as e:
        logger.warning(f"Failed to trigger state enforcement for {node_name}: {e}")

    # Poll until actual_state matches
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        database.refresh(ns)
        if ns.actual_state == target_actual:
            return {"status": "passed", "output": f"{node_name} is now {target_actual}", "error": None}

    return {
        "status": "failed",
        "output": f"Timeout after {timeout}s: {node_name} actual_state={ns.actual_state}, wanted={target_actual}",
        "error": None,
    }


async def _step_wait(step: dict, lab: models.Lab, database) -> dict:
    """Wait for a fixed number of seconds."""
    seconds = step.get("seconds", 5)
    await asyncio.sleep(seconds)
    return {"status": "passed", "output": f"Waited {seconds}s", "error": None}


async def _step_exec(step: dict, lab: models.Lab, database) -> dict:
    """Run a command on a node and optionally match expected output."""
    node_name = step.get("node", "")
    cmd = step.get("cmd", "")
    expect = step.get("expect")

    from app.tasks.test_runner import _resolve_agent_for_node
    agent, resolved_name = await _resolve_agent_for_node(database, lab, node_name)
    if not agent:
        return {"status": "error", "output": None, "error": f"Cannot resolve agent for node '{node_name}'"}

    resp = await agent_client.exec_node_on_agent(agent, lab.id, resolved_name, cmd)
    output = resp.get("output", "")
    exit_code = resp.get("exit_code", -1)

    if expect:
        if re.search(expect, output):
            return {"status": "passed", "output": output, "error": None}
        return {"status": "failed", "output": output, "error": f"Pattern '{expect}' not found in output"}

    if exit_code == 0:
        return {"status": "passed", "output": output, "error": None}
    return {"status": "failed", "output": output, "error": f"exit_code={exit_code}"}


# Register step handlers
_STEP_HANDLERS = {
    "verify": _step_verify,
    "link_down": _step_link_down,
    "link_up": _step_link_up,
    "node_stop": _step_node_stop,
    "node_start": _step_node_start,
    "wait": _step_wait,
    "exec": _step_exec,
}
