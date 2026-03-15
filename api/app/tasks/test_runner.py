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


# ---------------------------------------------------------------------------
# Vendor-aware exec routing helpers
# ---------------------------------------------------------------------------


def _resolve_node_vendor_config(database, lab: models.Lab, node_name: str):
    """Return the ``VendorConfig`` for a node, or ``None`` if unresolvable."""
    from app.models.topology import Node
    from app.services.device_service import get_resolver

    node = (
        database.query(Node)
        .filter(Node.lab_id == lab.id, Node.container_name == node_name)
        .first()
    )
    if not node or not node.device:
        return None
    return get_resolver().resolve_config(node.device)


def _resolve_node_exec_method(database, lab: models.Lab, node_name: str) -> tuple[str, str | None]:
    """Return ``(console_method, kind)`` for a node.

    Falls back to ``("docker_exec", None)`` when unresolvable.
    """
    cfg = _resolve_node_vendor_config(database, lab, node_name)
    if cfg:
        return (cfg.console_method, cfg.kind)
    return ("docker_exec", None)


def _build_ping_command(database, lab: models.Lab, node_name: str, target: str, count: int) -> str:
    """Build a vendor-appropriate ping command from VendorConfig.ping_command."""
    cfg = _resolve_node_vendor_config(database, lab, node_name)
    if cfg:
        return cfg.ping_command.format(target=target, count=count)
    # Fallback: Linux shell
    return f"ping -c {count} -W 2 {target}"


async def _exec_on_node(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    cmd: str,
    console_method: str,
    kind: str | None,
) -> dict:
    """Route command execution to the correct agent endpoint.

    Returns dict with keys ``exit_code`` (int) and ``output`` (str).
    """
    if console_method == "docker_exec":
        return await agent_client.exec_node_on_agent(agent, lab_id, node_name, cmd)
    # virsh / ssh — use cli-verify
    return await agent_client.cli_verify_on_agent(
        agent, lab_id, node_name, commands=[cmd], kind=kind,
    )


def _ping_succeeded(output: str, exit_code: int, console_method: str) -> bool:
    """Determine whether a ping test passed.

    For docker_exec the exit code is authoritative.  For CLI-based devices
    the command always "succeeds" (exit_code 0 means the CLI ran), so we
    parse the output for packet-loss indicators that work across NX-OS,
    IOS-XE, IOS-XR, and Junos.
    """
    if console_method == "docker_exec":
        return exit_code == 0

    # CLI output parsing — look for positive indicators
    if re.search(r"(\d+)\s+packets?\s+received", output) and not re.search(r"\b0\s+packets?\s+received", output):
        return True
    if re.search(r"0(\.0+)?%\s+packet\s+loss", output):
        return True
    return False


def _resolve_node_name(database, lab_id: str, name: str) -> str:
    """Resolve a node name that may be a display_name to its container_name.

    The frontend sends display names (e.g. ``CEOS-1``) but NodeState and
    placement use container names (e.g. ``ceos_1``).  Returns *name*
    unchanged when it already matches a container_name.
    """
    from app.models.topology import Node

    # Fast path: already a container_name?
    exists = (
        database.query(Node.container_name)
        .filter(Node.lab_id == lab_id, Node.container_name == name)
        .first()
    )
    if exists:
        return name

    # Try display_name lookup
    row = (
        database.query(Node.container_name)
        .filter(Node.lab_id == lab_id, Node.display_name == name)
        .first()
    )
    if row:
        return row[0]

    return name  # Return as-is; caller will handle "not found"


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
            node_name = _resolve_node_name(database, lab.id, spec.get("node_name") or spec.get("node") or "")
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
            source = _resolve_node_name(database, lab.id, spec.get("source") or "")
            target = spec.get("target")
            count = spec.get("count", 3)
            agent, node_name = await _resolve_agent_for_node(database, lab, source)
            if not agent:
                return _result(index, spec_name, "error", start, error=f"Cannot resolve agent for node '{source}'")
            console_method, kind = _resolve_node_exec_method(database, lab, node_name)
            cmd = _build_ping_command(database, lab, node_name, target, count)
            resp = await _exec_on_node(agent, lab.id, node_name, cmd, console_method, kind)
            output = resp.get("output", "")
            exit_code = resp.get("exit_code", -1)
            if _ping_succeeded(output, exit_code, console_method):
                return _result(index, spec_name, "passed", start, output=output)
            return _result(index, spec_name, "failed", start, output=output)

        elif spec_type == "command":
            node = _resolve_node_name(database, lab.id, spec.get("node") or "")
            cmd = spec.get("cmd", "")
            expect_pattern = spec.get("expect")
            agent, node_name = await _resolve_agent_for_node(database, lab, node)
            if not agent:
                return _result(index, spec_name, "error", start, error=f"Cannot resolve agent for node '{node}'")
            console_method, kind = _resolve_node_exec_method(database, lab, node_name)
            resp = await _exec_on_node(agent, lab.id, node_name, cmd, console_method, kind)
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
