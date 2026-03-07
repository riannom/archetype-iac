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


@pytest.mark.integration
@pytest.mark.ovs_smoke
@pytest.mark.skipif(os.getenv("OVS_SMOKE") != "1", reason="set OVS_SMOKE=1 to enable")
@pytest.mark.skipif(os.geteuid() != 0, reason="requires root (ip netns + OVS)")
@pytest.mark.skipif(shutil.which("ovs-vsctl") is None, reason="ovs-vsctl not available")
def test_ovs_bridge_patch_forwards_and_isolates_vlan_traffic():
    ovs_br = f"br-smoke-{uuid.uuid4().hex[:8]}"
    linux_br = f"br-linux-{uuid.uuid4().hex[:8]}"
    ns_ovs = f"ns-ovs-{uuid.uuid4().hex[:6]}"
    ns_linux = f"ns-lx-{uuid.uuid4().hex[:6]}"

    ovs_host = f"vethoh{uuid.uuid4().hex[:5]}"
    ovs_ns = f"vethon{uuid.uuid4().hex[:5]}"
    linux_host = f"vethlh{uuid.uuid4().hex[:5]}"
    linux_ns = f"vethln{uuid.uuid4().hex[:5]}"
    patch_ovs = f"ptov{uuid.uuid4().hex[:6]}"
    patch_linux = f"ptln{uuid.uuid4().hex[:6]}"

    try:
        _run(["ovs-vsctl", "--may-exist", "add-br", ovs_br])
        _run(["ip", "link", "add", linux_br, "type", "bridge"])
        _run(["ip", "link", "set", ovs_br, "up"])
        _run(["ip", "link", "set", linux_br, "up"])

        _run(["ip", "netns", "add", ns_ovs])
        _run(["ip", "netns", "add", ns_linux])

        _run(["ip", "link", "add", ovs_host, "type", "veth", "peer", "name", ovs_ns])
        _run(["ip", "link", "add", linux_host, "type", "veth", "peer", "name", linux_ns])
        _run(["ip", "link", "add", patch_ovs, "type", "veth", "peer", "name", patch_linux])

        _run(["ip", "link", "set", ovs_ns, "netns", ns_ovs])
        _run(["ip", "link", "set", linux_ns, "netns", ns_linux])

        _run(["ovs-vsctl", "--may-exist", "add-port", ovs_br, ovs_host, "tag=300"])
        _run(["ovs-vsctl", "--may-exist", "add-port", ovs_br, patch_ovs, "tag=300"])
        _run(["ip", "link", "set", patch_linux, "master", linux_br])
        _run(["ip", "link", "set", linux_host, "master", linux_br])

        _run(["ip", "link", "set", ovs_host, "up"])
        _run(["ip", "link", "set", patch_ovs, "up"])
        _run(["ip", "link", "set", patch_linux, "up"])
        _run(["ip", "link", "set", linux_host, "up"])

        _run(["ip", "netns", "exec", ns_ovs, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns_linux, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns_ovs, "ip", "addr", "add", "10.201.0.1/24", "dev", ovs_ns])
        _run(["ip", "netns", "exec", ns_linux, "ip", "addr", "add", "10.201.0.2/24", "dev", linux_ns])
        _run(["ip", "netns", "exec", ns_ovs, "ip", "link", "set", ovs_ns, "up"])
        _run(["ip", "netns", "exec", ns_linux, "ip", "link", "set", linux_ns, "up"])

        _run(["ip", "netns", "exec", ns_ovs, "ping", "-c", "1", "-W", "1", "10.201.0.2"])

        _run(["ovs-vsctl", "set", "port", patch_ovs, "tag=301"])
        _run(["ip", "netns", "exec", ns_ovs, "ip", "neigh", "flush", "all"], check=False)
        res = subprocess.run(
            ["ip", "netns", "exec", ns_ovs, "ping", "-c", "1", "-W", "1", "10.201.0.2"],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0

    finally:
        subprocess.run(["ip", "netns", "del", ns_ovs], capture_output=True, text=True)
        subprocess.run(["ip", "netns", "del", ns_linux], capture_output=True, text=True)
        subprocess.run(["ovs-vsctl", "--if-exists", "del-br", ovs_br], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", linux_br], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", ovs_host], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", linux_host], capture_output=True, text=True)
        subprocess.run(["ip", "link", "del", patch_ovs], capture_output=True, text=True)
