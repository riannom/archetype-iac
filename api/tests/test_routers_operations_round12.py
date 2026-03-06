"""Round 12 deep-path tests for api/app/routers/labs/operations.py.

Targets under-tested paths:
- Config diff: multiline hunks, empty content, large diffs, multi-hunk,
  content with special characters, cross-node snapshots, wrong lab ID
- Orphan cleanup: placements with multiple hosts, partial agent errors,
  agent returns errors in result dict
- Inventory: terraform format with host_name, topology export exception,
  malformed management_ips_json
- Interface mappings: link-state gap-fill logic
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(
    db: Session,
    lab_id: str,
    node_name: str,
    content: str,
    *,
    snapshot_type: str = "manual",
) -> models.ConfigSnapshot:
    """Create a config snapshot and return the refreshed ORM object."""
    s = models.ConfigSnapshot(
        lab_id=lab_id,
        node_name=node_name,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        snapshot_type=snapshot_type,
        created_at=datetime.now(timezone.utc),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _node_state(
    db: Session,
    lab_id: str,
    node_id: str,
    node_name: str,
    *,
    desired: str = "stopped",
    actual: str = "undeployed",
    is_ready: bool = False,
    management_ip: str | None = None,
    management_ips_json: str | None = None,
    error_message: str | None = None,
) -> models.NodeState:
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        is_ready=is_ready,
        management_ip=management_ip,
        management_ips_json=management_ips_json,
        error_message=error_message,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


def _node(
    db: Session,
    lab_id: str,
    node_id: str,
    gui_id: str,
    container_name: str,
    device: str = "linux",
    image: str | None = None,
) -> models.Node:
    n = models.Node(
        id=node_id,
        lab_id=lab_id,
        gui_id=gui_id,
        display_name=container_name,
        container_name=container_name,
        device=device,
        image=image,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _link_state(
    db: Session,
    lab_id: str,
    link_name: str,
    src_node: str,
    src_iface: str,
    tgt_node: str,
    tgt_iface: str,
    *,
    desired: str = "up",
    actual: str = "up",
    error_message: str | None = None,
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node=src_node,
        source_interface=src_iface,
        target_node=tgt_node,
        target_interface=tgt_iface,
        desired_state=desired,
        actual_state=actual,
        error_message=error_message,
    )
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return ls


def _placement(
    db: Session, lab_id: str, node_name: str, host_id: str
) -> models.NodePlacement:
    p = models.NodePlacement(
        lab_id=lab_id,
        node_name=node_name,
        host_id=host_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ============================================================================
# Config Diff Edge Cases
# ============================================================================


class TestConfigDiffEdgeCases:
    """Deep-path tests for the POST /labs/{lab_id}/config-diff endpoint."""

    def test_diff_empty_content_both_sides(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Two snapshots with empty content produce zero additions/deletions."""
        a = _snap(test_db, sample_lab.id, "R1", "")
        b = _snap(test_db, sample_lab.id, "R1", "")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] == 0
        assert data["deletions"] == 0
        assert data["diff_lines"] == []

    def test_diff_empty_to_nonempty(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Diffing empty snapshot A against non-empty B shows all additions."""
        a = _snap(test_db, sample_lab.id, "R1", "")
        b = _snap(test_db, sample_lab.id, "R1", "line1\nline2\nline3\n")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] == 3
        assert data["deletions"] == 0
        types = {dl["type"] for dl in data["diff_lines"]}
        assert "added" in types

    def test_diff_nonempty_to_empty(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Diffing non-empty A against empty B shows all deletions."""
        a = _snap(test_db, sample_lab.id, "R1", "alpha\nbeta\n")
        b = _snap(test_db, sample_lab.id, "R1", "")
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deletions"] == 2
        assert data["additions"] == 0
        types = {dl["type"] for dl in data["diff_lines"]}
        assert "removed" in types

    def test_diff_multi_hunk_produces_correct_line_numbers(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """A diff with changes in multiple non-adjacent regions creates
        multiple hunk headers and tracks line numbers correctly."""
        content_a = "\n".join([f"line{i}" for i in range(1, 21)]) + "\n"
        lines_b = [f"line{i}" for i in range(1, 21)]
        lines_b[2] = "CHANGED3"   # line3 -> CHANGED3
        lines_b[17] = "CHANGED18"  # line18 -> CHANGED18
        content_b = "\n".join(lines_b) + "\n"

        a = _snap(test_db, sample_lab.id, "R1", content_a)
        b = _snap(test_db, sample_lab.id, "R1", content_b)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Two changed lines = at least 2 additions + 2 deletions
        assert data["additions"] >= 2
        assert data["deletions"] >= 2

        # Verify header lines exist (hunk markers)
        headers = [dl for dl in data["diff_lines"] if dl["type"] == "header"]
        assert len(headers) >= 2  # at least file header + hunk header

        # Verify added/removed lines have correct line numbers set
        added = [dl for dl in data["diff_lines"] if dl["type"] == "added"]
        for a_line in added:
            assert a_line["line_number_b"] is not None
            assert a_line["line_number_a"] is None

        removed = [dl for dl in data["diff_lines"] if dl["type"] == "removed"]
        for r_line in removed:
            assert r_line["line_number_a"] is not None
            assert r_line["line_number_b"] is None

    def test_diff_special_characters_in_content(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Content with tabs, unicode, and special chars diffs correctly."""
        content_a = "hostname R1\n\tdescription 'link to R2'\n! comment\n"
        content_b = "hostname R1\n\tdescription \"link to R3\"\n! updated\n"

        a = _snap(test_db, sample_lab.id, "R1", content_a)
        b = _snap(test_db, sample_lab.id, "R1", content_b)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["additions"] >= 1
        assert data["deletions"] >= 1

    def test_diff_cross_node_snapshots(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Diffing snapshots from different nodes is allowed and works."""
        a = _snap(test_db, sample_lab.id, "R1", "hostname R1\nip route 0.0.0.0/0 10.0.0.1\n")
        b = _snap(test_db, sample_lab.id, "R2", "hostname R2\nip route 0.0.0.0/0 10.0.0.254\n")

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Both hostnames and routes differ
        assert data["additions"] >= 1
        assert data["deletions"] >= 1
        # Snapshot metadata preserved
        assert data["snapshot_a"]["node_name"] == "R1"
        assert data["snapshot_b"]["node_name"] == "R2"

    def test_diff_snapshot_from_different_lab_returns_404(
        self, test_client, auth_headers, test_db, sample_lab, test_user
    ):
        """Snapshot belonging to a different lab is not found (lab_id filter)."""
        other_lab = models.Lab(
            name="Other Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/other-lab",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        snap_other = _snap(test_db, other_lab.id, "R1", "config")
        snap_mine = _snap(test_db, sample_lab.id, "R1", "config")

        # Snapshot A belongs to other lab -> not found under sample_lab
        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": snap_other.id, "snapshot_id_b": snap_mine.id},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_diff_unchanged_lines_have_both_line_numbers(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """Unchanged context lines carry both line_number_a and line_number_b."""
        content_a = "line1\nline2\nline3\n"
        content_b = "line1\nCHANGED\nline3\n"
        a = _snap(test_db, sample_lab.id, "R1", content_a)
        b = _snap(test_db, sample_lab.id, "R1", content_b)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/config-diff",
            json={"snapshot_id_a": a.id, "snapshot_id_b": b.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        unchanged = [dl for dl in data["diff_lines"] if dl["type"] == "unchanged"]
        for u in unchanged:
            assert u["line_number_a"] is not None
            assert u["line_number_b"] is not None


# ============================================================================
# Cleanup Orphans Deep Paths
# ============================================================================


class TestCleanupOrphansDeepPaths:
    """Deep-path tests for POST /labs/{lab_id}/cleanup-orphans."""

    def test_cleanup_with_placements_sends_correct_keep_nodes(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Placements determine which nodes to keep on each agent."""
        import app.routers.labs as labs_pkg

        # Place two nodes on the sample_host
        _placement(test_db, sample_lab.id, "R1", sample_host.id)
        _placement(test_db, sample_lab.id, "R2", sample_host.id)

        captured_calls = []

        async def capture_cleanup(agent, lab_id, keep_nodes):
            captured_calls.append((agent.id, lab_id, sorted(keep_nodes)))
            return {"removed_containers": [], "errors": []}

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(side_effect=capture_cleanup)
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify that the agent was called with the correct keep_nodes
        agent_call = [c for c in captured_calls if c[0] == sample_host.id]
        assert len(agent_call) == 1
        assert agent_call[0][2] == ["R1", "R2"]

    def test_cleanup_agent_returns_errors_in_result(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Agent returns errors within the result dict (not an exception)."""
        import app.routers.labs as labs_pkg

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(
            return_value={
                "removed_containers": ["old-ctr"],
                "errors": ["permission denied on ctr-x"],
            }
        )
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Both removed containers and errors appear in the response
        assert "old-ctr" in data["removed_by_agent"][sample_host.name]
        assert len(data["errors"]) == 1
        assert "permission denied" in data["errors"][0]

    def test_cleanup_multiple_agents_partial_failure(
        self, test_client, auth_headers, test_db, sample_lab, multiple_hosts, monkeypatch
    ):
        """One agent succeeds, another fails; both results are captured."""
        import app.routers.labs as labs_pkg

        call_count = {"n": 0}

        async def mixed_cleanup(agent, lab_id, keep_nodes):
            call_count["n"] += 1
            if agent.id == "agent-1":
                return {"removed_containers": ["orphan-1"], "errors": []}
            else:
                raise RuntimeError("agent-2 unreachable")

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(side_effect=mixed_cleanup)
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Agent 1 succeeded
        assert "Agent 1" in data["removed_by_agent"]
        assert data["removed_by_agent"]["Agent 1"] == ["orphan-1"]
        # Agent 2 errored (agent-3 is offline so not queried)
        assert len(data["errors"]) == 1
        assert "agent-2 unreachable" in data["errors"][0]

    def test_cleanup_no_removed_containers_from_agent(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Agent returns no removed containers -> removed_by_agent stays empty for that agent."""
        import app.routers.labs as labs_pkg

        mock_ac = MagicMock()
        mock_ac.cleanup_lab_orphans = AsyncMock(
            return_value={"removed_containers": [], "errors": []}
        )
        monkeypatch.setattr(labs_pkg, "agent_client", mock_ac)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/cleanup-orphans",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Empty list means agent name should NOT appear in removed_by_agent
        assert data["removed_by_agent"] == {}
        assert data["errors"] == []


# ============================================================================
# Inventory Export Deep Paths
# ============================================================================


class TestInventoryExportDeepPaths:
    """Deep-path tests for GET /labs/{lab_id}/inventory."""

    def test_terraform_includes_host_name(
        self, test_client, auth_headers, test_db, sample_lab, sample_host, monkeypatch
    ):
        """Terraform format includes host field when placement exists."""
        _node_state(
            test_db, sample_lab.id, "n1", "R1",
            desired="running", actual="running", management_ip="10.0.0.5",
        )
        _placement(test_db, sample_lab.id, "R1", sample_host.id)

        import app.routers.labs as labs_pkg
        mock_ts = MagicMock()
        mock_ts.return_value.has_nodes.return_value = False
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=terraform",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        tf = json.loads(data["content"])
        assert tf["lab_nodes"]["R1"]["host"] == sample_host.name

    def test_inventory_topology_export_exception_handled(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        """If topology export raises, inventory still returns (no device_type)."""
        _node_state(test_db, sample_lab.id, "n1", "R1")

        # Patch _ensure_node_states_exist to a no-op so the mock TopologyService
        # is only exercised by the inventory endpoint's own try/except block.
        import app.routers.labs.operations as ops_mod
        monkeypatch.setattr(ops_mod, "_ensure_node_states_exist", lambda db, lid: None)

        import app.routers.labs as labs_pkg
        mock_ts = MagicMock()
        mock_ts_inst = mock_ts.return_value
        mock_ts_inst.has_nodes.return_value = True
        mock_ts_inst.export_to_graph.side_effect = Exception("corrupt topology")
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["device_type"] is None

    def test_inventory_malformed_management_ips_json(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        """Malformed management_ips_json does not crash; all_ips defaults to []."""
        _node_state(
            test_db, sample_lab.id, "n1", "R1",
            management_ips_json="not-valid-json{{{",
        )

        import app.routers.labs as labs_pkg
        mock_ts = MagicMock()
        mock_ts.return_value.has_nodes.return_value = False
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=json",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"][0]["all_ips"] == []

    def test_ansible_device_os_mapping(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        """Ansible format maps known device types to ansible_network_os."""
        _node_state(
            test_db, sample_lab.id, "n1", "R1",
            management_ip="10.0.0.1",
        )

        # Patch _ensure_node_states_exist to a no-op so the mock graph nodes
        # (with MagicMock IDs) don't get upserted into SQLite.
        import app.routers.labs.operations as ops_mod
        monkeypatch.setattr(ops_mod, "_ensure_node_states_exist", lambda db, lid: None)

        mock_graph_node = MagicMock()
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph_node.device = "ceos"

        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        import app.routers.labs as labs_pkg
        mock_ts = MagicMock()
        mock_ts_inst = mock_ts.return_value
        mock_ts_inst.has_nodes.return_value = True
        mock_ts_inst.export_to_graph.return_value = mock_graph
        monkeypatch.setattr(labs_pkg, "TopologyService", mock_ts)

        resp = test_client.get(
            f"/labs/{sample_lab.id}/inventory?format=ansible",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        import yaml as _yaml
        content = _yaml.safe_load(resp.json()["content"])
        host_vars = content["all"]["hosts"]["R1"]
        assert host_vars["ansible_network_os"] == "arista.eos.eos"
        assert host_vars["device_type"] == "ceos"


# ============================================================================
# Interface Mappings Gap-Fill from LinkStates
# ============================================================================


class TestInterfaceMappingsGapFill:
    """Tests for GET /labs/{lab_id}/interface-mappings link-state gap-fill."""

    def test_gap_fill_from_link_states(
        self, test_client, auth_headers, test_db, sample_lab, monkeypatch
    ):
        """Interfaces found only in link_states are added to the response."""
        # Create nodes (needed for device lookup)
        _node(test_db, sample_lab.id, "nd1", "g1", "R1", device="ceos")
        _node(test_db, sample_lab.id, "nd2", "g2", "R2", device="srl")

        # Create a link_state but NO InterfaceMapping entries
        _link_state(
            test_db, sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/interface-mappings",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Gap-fill should produce entries for R1:eth1 and R2:eth1
        assert data["total"] >= 2
        names = {(m["node_name"], m["linux_interface"]) for m in data["mappings"]}
        assert ("R1", "eth1") in names
        assert ("R2", "eth1") in names

    def test_gap_fill_does_not_duplicate_existing_mappings(
        self, test_client, auth_headers, test_db, sample_lab
    ):
        """If InterfaceMapping already exists, link_state gap-fill skips it."""
        nd = _node(test_db, sample_lab.id, "nd1", "g1", "R1", device="linux")

        # Existing mapping
        im = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=nd.id,
            linux_interface="eth1",
            ovs_port="vh-1234",
            ovs_bridge="arch-ovs",
            vlan_tag=100,
        )
        test_db.add(im)
        test_db.commit()

        # Link state referencing same node/interface
        _link_state(
            test_db, sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
        )

        resp = test_client.get(
            f"/labs/{sample_lab.id}/interface-mappings",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # R1:eth1 appears only once (from InterfaceMapping, not duplicated by gap-fill)
        r1_eth1 = [
            m for m in data["mappings"]
            if m.get("node_name") == "R1" and m["linux_interface"] == "eth1"
        ]
        assert len(r1_eth1) == 1
