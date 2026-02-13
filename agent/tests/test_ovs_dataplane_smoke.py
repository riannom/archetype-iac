"""Opt-in OVS dataplane smoke test.

This is a host-only integration smoke test that validates basic L2 adjacency
through OVS with access VLAN tagging.

Disabled by default. Enable with:
  OVS_SMOKE=1 sudo -E pytest -q agent/tests/test_ovs_dataplane_smoke.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


@pytest.mark.integration
@pytest.mark.ovs_smoke
@pytest.mark.skipif(os.getenv("OVS_SMOKE") != "1", reason="set OVS_SMOKE=1 to enable")
@pytest.mark.skipif(os.geteuid() != 0, reason="requires root (ip netns + OVS)")
@pytest.mark.skipif(shutil.which("ovs-vsctl") is None, reason="ovs-vsctl not available")
def test_ovs_vlan_tag_allows_connectivity_and_isolation():
    br = f"br-smoke-{uuid.uuid4().hex[:8]}"
    ns1 = f"ns-smoke1-{uuid.uuid4().hex[:6]}"
    ns2 = f"ns-smoke2-{uuid.uuid4().hex[:6]}"

    # Host-side veth ends that connect to OVS.
    h1 = f"vethh1{uuid.uuid4().hex[:6]}"
    h2 = f"vethh2{uuid.uuid4().hex[:6]}"
    # Namespace-side ends.
    n1 = f"vethn1{uuid.uuid4().hex[:6]}"
    n2 = f"vethn2{uuid.uuid4().hex[:6]}"

    try:
        _run(["ovs-vsctl", "--may-exist", "add-br", br])
        _run(["ip", "link", "set", br, "up"])

        _run(["ip", "netns", "add", ns1])
        _run(["ip", "netns", "add", ns2])

        _run(["ip", "link", "add", h1, "type", "veth", "peer", "name", n1])
        _run(["ip", "link", "add", h2, "type", "veth", "peer", "name", n2])

        _run(["ip", "link", "set", n1, "netns", ns1])
        _run(["ip", "link", "set", n2, "netns", ns2])

        _run(["ovs-vsctl", "--may-exist", "add-port", br, h1, "tag=100"])
        _run(["ovs-vsctl", "--may-exist", "add-port", br, h2, "tag=100"])

        _run(["ip", "link", "set", h1, "up"])
        _run(["ip", "link", "set", h2, "up"])

        _run(["ip", "netns", "exec", ns1, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns2, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns1, "ip", "addr", "add", "10.200.0.1/24", "dev", n1])
        _run(["ip", "netns", "exec", ns2, "ip", "addr", "add", "10.200.0.2/24", "dev", n2])
        _run(["ip", "netns", "exec", ns1, "ip", "link", "set", n1, "up"])
        _run(["ip", "netns", "exec", ns2, "ip", "link", "set", n2, "up"])

        # Same VLAN tag should pass.
        _run(["ip", "netns", "exec", ns1, "ping", "-c", "1", "-W", "1", "10.200.0.2"])

        # Different VLAN tags should isolate.
        _run(["ovs-vsctl", "set", "port", h2, "tag=200"])
        # Ensure no stale neighbor state masks the isolation.
        _run(["ip", "netns", "exec", ns1, "ip", "neigh", "flush", "all"], check=False)
        res = subprocess.run(
            ["ip", "netns", "exec", ns1, "ping", "-c", "1", "-W", "1", "10.200.0.2"],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0

    finally:
        # Best-effort cleanup.
        subprocess.run(["ip", "netns", "del", ns1], capture_output=True, text=True)
        subprocess.run(["ip", "netns", "del", ns2], capture_output=True, text=True)
        subprocess.run(["ovs-vsctl", "--if-exists", "del-br", br], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", h1], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", h2], capture_output=True, text=True)
