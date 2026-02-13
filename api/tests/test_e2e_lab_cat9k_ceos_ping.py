"""Opt-in nightly E2E: cat9000v-q200 <-> ceos-3 ping.

This test is intentionally skipped in normal CI because it requires:
- a running API at E2E_API_URL
- a lab with the specified nodes
- local access to docker + libvirt (virsh)

Enable with:
  E2E_PING=1 E2E_LAB_ID=... pytest -q api/tests/test_e2e_lab_cat9k_ceos_ping.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import asyncio

import httpx
import pytest


def _safe_lab_id(lab_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", lab_id)[:20]


def _docker_container_name(lab_id: str, node_name: str) -> str:
    return f"archetype-{_safe_lab_id(lab_id)}-{re.sub(r'[^a-zA-Z0-9_-]', '', node_name)}"


def _libvirt_domain_name(lab_id: str, node_name: str) -> str:
    safe_node = re.sub(r"[^a-zA-Z0-9_-]", "", node_name)[:30]
    return f"arch-{_safe_lab_id(lab_id)}-{safe_node}"


async def _login(api_url: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.post(
            f"{api_url}/auth/login",
            data={"username": username, "password": password},
        )
        res.raise_for_status()
        return res.json()["access_token"]


async def _wait_job(api_url: str, token: str, lab_id: str, job_id: str, timeout_s: int = 900) -> None:
    if not job_id:
        return
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout_s
    async with httpx.AsyncClient(timeout=20.0) as client:
        while time.time() < deadline:
            res = await client.get(f"{api_url}/labs/{lab_id}/jobs/{job_id}", headers=headers)
            res.raise_for_status()
            status = res.json().get("status")
            if status in {"completed", "failed", "cancelled"}:
                assert status == "completed"
                return
            await asyncio.sleep(2)
    raise AssertionError(f"Job {job_id} did not complete within {timeout_s}s")


@pytest.mark.e2e
@pytest.mark.skipif(os.getenv("E2E_PING") != "1", reason="set E2E_PING=1 to enable")
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
@pytest.mark.skipif(shutil.which("virsh") is None, reason="virsh CLI not available")
@pytest.mark.asyncio
async def test_e2e_cat9k_ceos_ping_roundtrip():
    lab_id = os.getenv("E2E_LAB_ID")
    assert lab_id, "E2E_LAB_ID is required"

    api_url = os.getenv("E2E_API_URL", "http://localhost:8000")
    username = os.getenv("E2E_ADMIN_EMAIL", os.getenv("ADMIN_EMAIL", "admin@example.com"))
    password = os.getenv("E2E_ADMIN_PASSWORD", os.getenv("ADMIN_PASSWORD", "changeme123"))

    cat_node = os.getenv("E2E_CAT_NODE", "cat9000v_q200_8")
    ceos_node = os.getenv("E2E_CEOS_NODE", "ceos_3")
    cat_ip = os.getenv("E2E_CAT_IP", "10.3.8.8")
    ceos_ip = os.getenv("E2E_CEOS_IP", "10.3.8.3")

    # Skip if we don't have permissions to talk to libvirt.
    virsh_check = subprocess.run(
        ["virsh", "-c", "qemu:///system", "list", "--all"],
        capture_output=True,
        text=True,
    )
    if virsh_check.returncode != 0 and "Operation not permitted" in (virsh_check.stderr or ""):
        pytest.skip("no permission to access libvirt (qemu:///system)")

    token = await _login(api_url, username, password)
    headers = {"Authorization": f"Bearer {token}"}

    # Resolve node IDs from node states endpoint.
    async with httpx.AsyncClient(timeout=30.0) as client:
        states = await client.get(f"{api_url}/labs/{lab_id}/nodes/states", headers=headers)
        states.raise_for_status()
        nodes = states.json()["nodes"]
        by_name = {n["node_name"]: n for n in nodes}

        assert cat_node in by_name
        assert ceos_node in by_name
        cat_id = by_name[cat_node]["node_id"]
        ceos_id = by_name[ceos_node]["node_id"]

        # Force desired running for both.
        await client.put(
            f"{api_url}/labs/{lab_id}/nodes/{cat_id}/desired-state",
            headers=headers,
            json={"state": "running"},
        )
        await client.put(
            f"{api_url}/labs/{lab_id}/nodes/{ceos_id}/desired-state",
            headers=headers,
            json={"state": "running"},
        )

        # Trigger reconcile jobs (if needed).
        r1 = await client.post(f"{api_url}/labs/{lab_id}/nodes/{cat_id}/reconcile", headers=headers)
        r1.raise_for_status()
        r2 = await client.post(f"{api_url}/labs/{lab_id}/nodes/{ceos_id}/reconcile", headers=headers)
        r2.raise_for_status()

        await _wait_job(api_url, token, lab_id, r1.json().get("job_id", ""), timeout_s=900)
        await _wait_job(api_url, token, lab_id, r2.json().get("job_id", ""), timeout_s=900)

    ceos_container = _docker_container_name(lab_id, ceos_node)
    cat_domain = _libvirt_domain_name(lab_id, cat_node)

    # ceos -> cat
    ceos_ping = subprocess.run(
        [
            "docker",
            "exec",
            ceos_container,
            "Cli",
            "-p",
            "15",
            "-c",
            f"ping {cat_ip} source {ceos_ip} repeat 3",
        ],
        capture_output=True,
        text=True,
    )
    assert ceos_ping.returncode == 0, ceos_ping.stderr
    assert "0% packet loss" in ceos_ping.stdout

    # cat -> ceos (best-effort, via serial console)
    if os.getenv("E2E_CAT_TO_CEOS") == "0":
        pytest.skip("cat->ceos ping disabled via E2E_CAT_TO_CEOS=0")

    if shutil.which("python3") is None:
        pytest.skip("python3 not available for pexpect")

    import pexpect

    child = pexpect.spawn(
        f"virsh -c qemu:///system console --force {cat_domain}",
        timeout=30,
        encoding="utf-8",
    )
    try:
        child.expect("Connected to domain", timeout=15)
        # Wake prompt
        child.send("\r")
        child.send("\r")
        child.sendline(f"ping {ceos_ip} repeat 3")
        # IOS-XE style: "Success rate is 100 percent". If it doesn't appear, fail.
        child.expect(r"Success +rate +is +100 +percent", timeout=60)
    finally:
        try:
            child.sendcontrol("]")
        except Exception:
            pass
        child.close(force=True)
