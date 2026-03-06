"""Round 12 tests for link_states router private helpers and under-tested paths.

Covers: _choose_preferred_link_state, _find_matching_link_state,
_find_matching_link_state_by_endpoints, _parse_link_id_endpoints,
_link_endpoint_payload, _get_or_create_link_definition, _upsert_link_states,
_raise_link_endpoint_conflict, bulk desired-state endpoint, refresh endpoint,
cross-host link detail, and error paths.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

import app.routers.labs as _labs_pkg
from app import models, schemas
from app.routers.labs.link_states import (
    _choose_preferred_link_state,
    _find_matching_link_state,
    _find_matching_link_state_by_endpoints,
    _parse_link_id_endpoints,
    _link_endpoint_payload,
    _get_or_create_link_definition,
    _raise_link_endpoint_conflict,
)
from app.auth import create_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers_for(user):
    token = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def _make_link_state(
    *,
    id="ls-1",
    lab_id="lab-1",
    link_definition_id="ld-1",
    link_name="A:eth1-B:eth1",
    source_node="A",
    source_interface="eth1",
    target_node="B",
    target_interface="eth1",
    desired_state="up",
    actual_state="up",
    updated_at=None,
):
    """Build a minimal LinkState model instance for unit tests."""
    ls = models.LinkState(
        id=id,
        lab_id=lab_id,
        link_definition_id=link_definition_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        desired_state=desired_state,
        actual_state=actual_state,
    )
    ls.updated_at = updated_at
    return ls


def _set_lab_state(test_db, lab, state):
    lab.state = state
    test_db.commit()
    test_db.refresh(lab)


def _add_nodes(test_db, lab, host):
    """Create two Node rows (R1, R2) and return them."""
    n1 = models.Node(
        id="nd-r12-1", lab_id=lab.id, gui_id="g1", display_name="R1",
        container_name="R1", device="linux", host_id=host.id,
    )
    n2 = models.Node(
        id="nd-r12-2", lab_id=lab.id, gui_id="g2", display_name="R2",
        container_name="R2", device="linux", host_id=host.id,
    )
    test_db.add_all([n1, n2])
    test_db.flush()
    return n1, n2


def _add_link_def_and_state(test_db, lab, host, n1, n2, *, link_name="R1:eth1-R2:eth1"):
    """Create a Link definition + LinkState."""
    link_def = models.Link(
        lab_id=lab.id, link_name=link_name,
        source_node_id=n1.id, source_interface="eth1",
        target_node_id=n2.id, target_interface="eth1",
    )
    test_db.add(link_def)
    test_db.flush()

    ls = models.LinkState(
        lab_id=lab.id, link_definition_id=link_def.id,
        link_name=link_name,
        source_node="R1", source_interface="eth1",
        target_node="R2", target_interface="eth1",
        desired_state="up", actual_state="up",
        source_host_id=host.id, target_host_id=host.id,
        vlan_tag=100,
    )
    test_db.add(ls)
    test_db.commit()
    return link_def, ls


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def _patch_pkg(monkeypatch, sample_host):
    """Patch heavy dependencies resolved via _pkg() on the labs package."""
    mock_agent_client = MagicMock()
    mock_agent_client.hot_connect_on_agent = AsyncMock(return_value={"success": True, "vlan_tag": 200})
    mock_agent_client.hot_disconnect_on_agent = AsyncMock(return_value={"success": True})
    mock_agent_client.list_links_on_agent = AsyncMock(return_value={"links": []})
    monkeypatch.setattr(_labs_pkg, "agent_client", mock_agent_client)

    mock_get_agent = AsyncMock(return_value=sample_host)
    monkeypatch.setattr(_labs_pkg, "get_online_agent_for_lab", mock_get_agent)

    mock_reconcile = AsyncMock(return_value={
        "checked": 0, "valid": 0, "repaired": 0, "errors": 0, "skipped": 0,
    })
    monkeypatch.setattr(_labs_pkg, "reconcile_lab_links", mock_reconcile)

    mock_build_host_map = AsyncMock(return_value={"host-1": sample_host})
    monkeypatch.setattr(_labs_pkg, "_build_host_to_agent_map", mock_build_host_map)

    mock_create_link = AsyncMock(return_value=True)
    monkeypatch.setattr(_labs_pkg, "create_link_if_ready", mock_create_link)

    mock_teardown_link = AsyncMock(return_value=True)
    monkeypatch.setattr(_labs_pkg, "teardown_link", mock_teardown_link)

    monkeypatch.setattr(
        _labs_pkg, "sync_link_endpoint_reservations", lambda db, state: (True, [])
    )
    monkeypatch.setattr(
        _labs_pkg, "recompute_link_oper_state", lambda db, state: None
    )

    return {
        "agent_client": mock_agent_client,
        "get_online_agent_for_lab": mock_get_agent,
        "build_host_map": mock_build_host_map,
        "create_link_if_ready": mock_create_link,
        "teardown_link": mock_teardown_link,
    }


# ===================================================================
# 1. _choose_preferred_link_state — canonical row preference
# ===================================================================

class TestChoosePreferredLinkState:
    """Unit tests for _choose_preferred_link_state."""

    def test_prefers_canonical_name_over_non_canonical(self):
        """A state whose link_name matches the generated canonical name wins."""
        canonical = _make_link_state(
            id="ls-canon",
            link_name="A:eth1-B:eth1",
            source_node="A", source_interface="eth1",
            target_node="B", target_interface="eth1",
            desired_state="up",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        non_canonical = _make_link_state(
            id="ls-noncanon",
            link_name="B:eth1-A:eth1",  # reversed — not canonical
            source_node="B", source_interface="eth1",
            target_node="A", target_interface="eth1",
            desired_state="up",
            updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        preferred = _choose_preferred_link_state([canonical, non_canonical])
        assert preferred.id == "ls-canon"

    def test_prefers_non_deleted_over_deleted(self):
        """Among equally canonical rows, non-deleted wins."""
        active = _make_link_state(
            id="ls-active",
            desired_state="up",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        deleted = _make_link_state(
            id="ls-deleted",
            desired_state="deleted",
            updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        preferred = _choose_preferred_link_state([active, deleted])
        assert preferred.id == "ls-active"

    def test_prefers_most_recently_updated_as_tiebreaker(self):
        """When canonical status and deletion status tie, most recent wins."""
        older = _make_link_state(
            id="ls-older",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        newer = _make_link_state(
            id="ls-newer",
            updated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        preferred = _choose_preferred_link_state([older, newer])
        assert preferred.id == "ls-newer"

    def test_handles_none_updated_at(self):
        """Rows with updated_at=None should not crash and sort last."""
        with_ts = _make_link_state(
            id="ls-ts",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        without_ts = _make_link_state(
            id="ls-no-ts",
            updated_at=None,
        )
        preferred = _choose_preferred_link_state([with_ts, without_ts])
        assert preferred.id == "ls-ts"


# ===================================================================
# 2. _find_matching_link_state — dedup by link_definition_id
# ===================================================================

class TestFindMatchingLinkState:
    """Unit tests for _find_matching_link_state."""

    def test_returns_none_when_no_match(self):
        """No states match the given link_definition_id."""
        states = [_make_link_state(link_definition_id="ld-other")]
        preferred, duplicates = _find_matching_link_state(states, "ld-999")
        assert preferred is None
        assert duplicates == []

    def test_returns_single_match_with_no_duplicates(self):
        """Single match returns the state and empty duplicates."""
        ls = _make_link_state(link_definition_id="ld-1")
        preferred, duplicates = _find_matching_link_state([ls], "ld-1")
        assert preferred is ls
        assert duplicates == []

    def test_returns_preferred_and_duplicates_for_multiple_matches(self):
        """Multiple rows sharing a link_definition_id: preferred + duplicates."""
        ls1 = _make_link_state(
            id="ls-1", link_definition_id="ld-dup",
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        ls2 = _make_link_state(
            id="ls-2", link_definition_id="ld-dup",
            updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        preferred, duplicates = _find_matching_link_state([ls1, ls2], "ld-dup")
        assert preferred is not None
        assert len(duplicates) == 1
        assert duplicates[0].id != preferred.id


# ===================================================================
# 3. _find_matching_link_state_by_endpoints — legacy matching
# ===================================================================

class TestFindMatchingLinkStateByEndpoints:
    """Unit tests for endpoint-based matching (legacy rows without link_definition_id)."""

    def test_matches_by_canonical_endpoints(self):
        """Finds a row whose canonicalized endpoints match."""
        ls = _make_link_state(
            link_definition_id=None,
            source_node="A", source_interface="eth1",
            target_node="B", target_interface="eth2",
        )
        # Canonical order: A:eth1 < B:eth2, so key = (A, eth1, B, eth2)
        preferred, duplicates = _find_matching_link_state_by_endpoints(
            [ls], "A", "eth1", "B", "eth2",
        )
        assert preferred is ls
        assert duplicates == []

    def test_no_match_returns_none(self):
        ls = _make_link_state(
            link_definition_id=None,
            source_node="X", source_interface="eth9",
            target_node="Y", target_interface="eth9",
        )
        preferred, duplicates = _find_matching_link_state_by_endpoints(
            [ls], "A", "eth1", "B", "eth2",
        )
        assert preferred is None
        assert duplicates == []


# ===================================================================
# 4. _parse_link_id_endpoints
# ===================================================================

class TestParseLinkIdEndpoints:
    """Unit tests for _parse_link_id_endpoints."""

    def test_valid_format(self):
        result = _parse_link_id_endpoints("R1:eth1-R2:eth2")
        assert result is not None
        src_n, src_i, tgt_n, tgt_i = result
        # Canonicalized: R1:eth1 < R2:eth2
        assert src_n == "R1"
        assert src_i == "eth1"
        assert tgt_n == "R2"
        assert tgt_i == "eth2"

    def test_returns_none_without_dash(self):
        assert _parse_link_id_endpoints("R1:eth1") is None

    def test_returns_none_without_colon(self):
        assert _parse_link_id_endpoints("R1-R2") is None

    def test_returns_none_for_empty_components(self):
        assert _parse_link_id_endpoints(":eth1-R2:eth2") is None

    def test_reversed_order_canonicalizes(self):
        """Endpoints are canonicalized regardless of input order."""
        result = _parse_link_id_endpoints("Z:eth3-A:eth1")
        assert result is not None
        src_n, src_i, tgt_n, tgt_i = result
        assert src_n == "A"
        assert src_i == "eth1"
        assert tgt_n == "Z"
        assert tgt_i == "eth3"

    def test_colon_in_left_only(self):
        """Dash present but right side has no colon."""
        assert _parse_link_id_endpoints("R1:eth1-R2eth2") is None


# ===================================================================
# 5. _link_endpoint_payload
# ===================================================================

class TestLinkEndpointPayload:

    def test_returns_two_endpoint_dicts(self):
        ls = _make_link_state(
            source_node="NodeA", source_interface="eth3",
            target_node="NodeB", target_interface="eth4",
        )
        payload = _link_endpoint_payload(ls)
        assert len(payload) == 2
        assert payload[0]["node_name"] == "NodeA"
        assert payload[0]["interface_name"] == "eth3"
        assert payload[1]["node_name"] == "NodeB"
        assert payload[1]["interface_name"] == "eth4"

    def test_handles_none_interface(self):
        """When interface fields are None, normalize_interface gets empty string."""
        ls = _make_link_state(
            source_node="X", source_interface=None,
            target_node="Y", target_interface=None,
        )
        # Should not raise
        payload = _link_endpoint_payload(ls)
        assert len(payload) == 2


# ===================================================================
# 6. _get_or_create_link_definition — DB-backed tests
# ===================================================================

class TestGetOrCreateLinkDefinition:
    """Tests using the real test database session."""

    def test_creates_new_link_definition(self, test_db, sample_lab, sample_host):
        n1, n2 = _add_nodes(test_db, sample_lab, sample_host)
        link = _get_or_create_link_definition(
            test_db, sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
            node_by_name={"R1": n1, "R2": n2},
        )
        assert link is not None
        assert link.link_name == "R1:eth1-R2:eth1"
        assert link.source_node_id == n1.id
        assert link.target_node_id == n2.id

    def test_returns_existing_link_definition(self, test_db, sample_lab, sample_host):
        n1, n2 = _add_nodes(test_db, sample_lab, sample_host)
        existing = models.Link(
            lab_id=sample_lab.id, link_name="R1:eth1-R2:eth1",
            source_node_id=n1.id, source_interface="eth1",
            target_node_id=n2.id, target_interface="eth1",
        )
        test_db.add(existing)
        test_db.flush()

        result = _get_or_create_link_definition(
            test_db, sample_lab.id, "R1:eth1-R2:eth1",
            "R1", "eth1", "R2", "eth1",
        )
        assert result.id == existing.id

    def test_returns_none_when_nodes_missing_non_strict(self, test_db, sample_lab):
        result = _get_or_create_link_definition(
            test_db, sample_lab.id, "Ghost:eth1-Phantom:eth1",
            "Ghost", "eth1", "Phantom", "eth1",
            strict=False,
        )
        assert result is None

    def test_raises_when_nodes_missing_strict(self, test_db, sample_lab):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _get_or_create_link_definition(
                test_db, sample_lab.id, "Ghost:eth1-Phantom:eth1",
                "Ghost", "eth1", "Phantom", "eth1",
                strict=True,
            )
        assert exc_info.value.status_code == 404


# ===================================================================
# 7. Bulk desired state — PUT /labs/{lab_id}/links/desired-state
# ===================================================================

class TestSetAllLinksDesiredState:
    """PUT /labs/{lab_id}/links/desired-state"""

    def test_set_all_links_down(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        n1, n2 = _add_nodes(test_db, sample_lab, sample_host)
        _add_link_def_and_state(test_db, sample_lab, sample_host, n1, n2)

        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/desired-state",
            json={"state": "down"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["links"]) >= 1
        for link in body["links"]:
            assert link["desired_state"] == "down"

    def test_set_all_links_up(
        self, test_client, test_db, sample_lab, sample_host, auth_headers, _patch_pkg, monkeypatch,
    ):
        n1, n2 = _add_nodes(test_db, sample_lab, sample_host)
        _add_link_def_and_state(test_db, sample_lab, sample_host, n1, n2)

        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/desired-state",
            json={"state": "up"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        for link in resp.json()["links"]:
            assert link["desired_state"] == "up"

    def test_bulk_desired_state_lab_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.put(
            "/labs/nonexistent-lab/links/desired-state",
            json={"state": "down"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_bulk_desired_state_no_auth(
        self, test_client, sample_lab, _patch_pkg,
    ):
        resp = test_client.put(
            f"/labs/{sample_lab.id}/links/desired-state",
            json={"state": "down"},
        )
        assert resp.status_code == 401


# ===================================================================
# 8. Refresh link states — POST /labs/{lab_id}/links/refresh
# ===================================================================

class TestRefreshLinkStates:
    """POST /labs/{lab_id}/links/refresh"""

    def test_refresh_no_topology(
        self, test_client, test_db, sample_lab, auth_headers, _patch_pkg, monkeypatch,
    ):
        """Refresh with no topology returns 404."""
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        resp = test_client.post(
            f"/labs/{sample_lab.id}/links/refresh",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_refresh_lab_not_found(
        self, test_client, auth_headers, _patch_pkg,
    ):
        resp = test_client.post(
            "/labs/nonexistent-lab/links/refresh",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ===================================================================
# 9. Cross-host link detail with VXLAN tunnel
# ===================================================================

class TestCrossHostLinkDetail:
    """GET /labs/{lab_id}/links/{link_name}/detail for cross-host links."""

    def test_cross_host_detail_with_tunnel(
        self, test_client, test_db, sample_lab, multiple_hosts,
        sample_cross_host_link_state, sample_vxlan_tunnel,
        auth_headers, _patch_pkg, monkeypatch,
    ):
        """Cross-host link detail should include tunnel information."""
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        link_name = sample_cross_host_link_state.link_name
        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/{link_name}/detail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_cross_host"] is True
        assert body["tunnel"] is not None
        assert body["tunnel"]["vni"] == 12345
        assert body["tunnel"]["status"] == "active"
        assert body["source"]["host_id"] == multiple_hosts[0].id
        assert body["target"]["host_id"] == multiple_hosts[1].id

    def test_cross_host_detail_no_tunnel(
        self, test_client, test_db, sample_lab, multiple_hosts,
        sample_cross_host_link_state,
        auth_headers, _patch_pkg, monkeypatch,
    ):
        """Cross-host link without tunnel record should have tunnel=None."""
        mock_service = MagicMock()
        mock_service.has_nodes.return_value = False
        monkeypatch.setattr(_labs_pkg, "TopologyService", lambda db: mock_service)

        link_name = sample_cross_host_link_state.link_name
        resp = test_client.get(
            f"/labs/{sample_lab.id}/links/{link_name}/detail",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_cross_host"] is True
        assert body["tunnel"] is None


# ===================================================================
# 10. _raise_link_endpoint_conflict
# ===================================================================

class TestRaiseLinkEndpointConflict:
    """Tests for the conflict-raising helper."""

    def test_raises_409_with_conflict_details(self, test_db, sample_lab):
        """The helper should raise HTTPException with status 409."""
        from fastapi import HTTPException

        ls = models.LinkState(
            id="conflict-ls",
            lab_id=sample_lab.id,
            link_name="A:eth1-B:eth1",
            source_node="A", source_interface="eth1",
            target_node="B", target_interface="eth1",
            desired_state="up", actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _raise_link_endpoint_conflict(
                test_db, ls, ["A:eth1-C:eth1"],
            )
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["code"] == "link_endpoint_reserved"
        assert "A:eth1-C:eth1" in detail["conflicting_links"]

    def test_conflict_uses_conflicting_link_details_when_available(self, test_db, sample_lab, sample_host):
        """When get_conflicting_link_details returns results, those are used."""
        from fastapi import HTTPException

        n1, n2 = _add_nodes(test_db, sample_lab, sample_host)
        link_def, ls = _add_link_def_and_state(test_db, sample_lab, sample_host, n1, n2)

        # Create a second link state that conflicts on the same endpoint
        n3 = models.Node(
            id="nd-r12-3", lab_id=sample_lab.id, gui_id="g3", display_name="R3",
            container_name="R3", device="linux", host_id=sample_host.id,
        )
        test_db.add(n3)
        test_db.flush()

        conflicting_def = models.Link(
            lab_id=sample_lab.id, link_name="R1:eth1-R3:eth1",
            source_node_id=n1.id, source_interface="eth1",
            target_node_id=n3.id, target_interface="eth1",
        )
        test_db.add(conflicting_def)
        test_db.flush()

        conflicting_ls = models.LinkState(
            lab_id=sample_lab.id, link_definition_id=conflicting_def.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1", source_interface="eth1",
            target_node="R3", target_interface="eth1",
            desired_state="up", actual_state="up",
        )
        test_db.add(conflicting_ls)
        test_db.commit()

        with pytest.raises(HTTPException) as exc_info:
            _raise_link_endpoint_conflict(test_db, ls, [])
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert detail["code"] == "link_endpoint_reserved"
        assert len(detail["link"]["endpoints"]) == 2
        assert detail["link"]["link_name"] == "R1:eth1-R2:eth1"
