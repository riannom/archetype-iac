"""Extended tests for app.services.link_reservations.

Covers conflict detection with normalized interfaces, idempotent ensure,
cross-node conflicts, health snapshot, and sync_link_endpoint_reservations.
"""
from __future__ import annotations


from app import models
from app.services.link_reservations import (
    _find_legacy_conflicts,
    claim_link_endpoints,
    get_conflicting_link_details,
    get_conflicting_link_names,
    get_link_endpoint_reservation_drift_counts,
    get_link_endpoint_reservation_health_snapshot,
    release_link_endpoint_reservations,
    sync_link_endpoint_reservations,
)


# ---------------------------------------------------------------------------
# Tests: release_link_endpoint_reservations
# ---------------------------------------------------------------------------

class TestReleaseReservations:
    def test_release_nonexistent_is_noop(self, test_db) -> None:
        """Release on nonexistent link_state_id should not error."""
        release_link_endpoint_reservations(test_db, "nonexistent-id")
        # No exception raised

    def test_release_removes_all_for_link(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-b:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="b",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        # Add reservations manually
        test_db.add(models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=link.id,
            node_name="a",
            interface_name="eth1",
        ))
        test_db.add(models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=link.id,
            node_name="b",
            interface_name="eth1",
        ))
        test_db.commit()

        release_link_endpoint_reservations(test_db, link.id)
        count = test_db.query(models.LinkEndpointReservation).filter(
            models.LinkEndpointReservation.link_state_id == link.id
        ).count()
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: claim_link_endpoints
# ---------------------------------------------------------------------------

class TestClaimLinkEndpoints:
    def test_claim_succeeds_for_fresh_link(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        ok, conflicts = claim_link_endpoints(test_db, link)
        test_db.commit()
        assert ok is True
        assert conflicts == []

        reservations = test_db.query(models.LinkEndpointReservation).filter(
            models.LinkEndpointReservation.link_state_id == link.id
        ).count()
        assert reservations == 2

    def test_claim_idempotent_on_reclaim(self, test_db, sample_lab) -> None:
        """Claiming already-claimed link returns success without duplicates."""
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        ok1, _ = claim_link_endpoints(test_db, link)
        test_db.commit()
        ok2, _ = claim_link_endpoints(test_db, link)
        test_db.commit()

        assert ok1 is True
        assert ok2 is True

        reservations = test_db.query(models.LinkEndpointReservation).filter(
            models.LinkEndpointReservation.link_state_id == link.id
        ).count()
        assert reservations == 2

    def test_claim_detects_cross_node_conflict(self, test_db, sample_lab) -> None:
        """Second link sharing r1:eth1 should conflict after first is claimed."""
        # Create and claim the first link
        link_a = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link_a)
        test_db.commit()

        ok_a, _ = claim_link_endpoints(test_db, link_a)
        test_db.commit()
        assert ok_a is True

        # Now create a conflicting link and try to claim it
        link_b = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r3:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r3",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link_b)
        test_db.commit()

        ok_b, conflicts = claim_link_endpoints(test_db, link_b)
        test_db.commit()

        assert ok_b is False
        assert len(conflicts) >= 1


# ---------------------------------------------------------------------------
# Tests: sync_link_endpoint_reservations
# ---------------------------------------------------------------------------

class TestSyncLinkEndpointReservations:
    def test_sync_up_claims(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="s:eth1-t:eth1",
            source_node="s",
            source_interface="eth1",
            target_node="t",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        ok, conflicts = sync_link_endpoint_reservations(test_db, link)
        test_db.commit()
        assert ok is True
        assert conflicts == []

    def test_sync_down_releases(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="s:eth1-t:eth1",
            source_node="s",
            source_interface="eth1",
            target_node="t",
            target_interface="eth1",
            desired_state="down",
            actual_state="down",
        )
        test_db.add(link)
        test_db.commit()

        # Pre-add a stale reservation
        test_db.add(models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=link.id,
            node_name="s",
            interface_name="eth1",
        ))
        test_db.commit()

        ok, conflicts = sync_link_endpoint_reservations(test_db, link)
        test_db.commit()
        assert ok is True

        count = test_db.query(models.LinkEndpointReservation).filter(
            models.LinkEndpointReservation.link_state_id == link.id
        ).count()
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: get_conflicting_link_names / get_conflicting_link_details
# ---------------------------------------------------------------------------

class TestConflictQueries:
    def test_get_conflicting_link_names_empty(self, test_db, sample_lab) -> None:
        result = get_conflicting_link_names(
            test_db, sample_lab.id, "fake-id", [("x", "eth1")]
        )
        assert result == []

    def test_get_conflicting_link_details_structure(self, test_db, sample_lab) -> None:
        link_a = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-b:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="b",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link_a)
        test_db.commit()

        # Claim link_a
        ok, _ = claim_link_endpoints(test_db, link_a)
        test_db.commit()
        assert ok

        # Check for conflicts against a:eth1
        details = get_conflicting_link_details(
            test_db, sample_lab.id, "fake-link-id", [("a", "eth1")]
        )
        assert len(details) == 1
        assert details[0]["node_name"] == "a"
        assert details[0]["interface_name"] == "eth1"
        assert "a:eth1-b:eth1" in details[0]["conflicting_links"]


# ---------------------------------------------------------------------------
# Tests: get_link_endpoint_reservation_drift_counts
# ---------------------------------------------------------------------------

class TestDriftCounts:
    def test_clean_state_all_zeros(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="x:eth1-y:eth1",
            source_node="x",
            source_interface="eth1",
            target_node="y",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        ok, _ = claim_link_endpoints(test_db, link)
        test_db.commit()
        assert ok

        drift = get_link_endpoint_reservation_drift_counts(test_db)
        assert drift["missing"] == 0
        assert drift["orphaned"] == 0
        assert drift["conflicts"] == 0
        assert drift["total"] == 2


# ---------------------------------------------------------------------------
# Tests: get_link_endpoint_reservation_health_snapshot
# ---------------------------------------------------------------------------

class TestHealthSnapshot:
    def test_healthy_snapshot(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="h1:eth1-h2:eth1",
            source_node="h1",
            source_interface="eth1",
            target_node="h2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        ok, _ = claim_link_endpoints(test_db, link)
        test_db.commit()
        assert ok

        snapshot = get_link_endpoint_reservation_health_snapshot(test_db)
        assert snapshot["counts"]["desired_up_links"] == 1
        assert snapshot["counts"]["expected_reservations"] == 2
        assert snapshot["counts"]["total_reservations"] == 2
        assert snapshot["counts"]["missing_reservations"] == 0
        assert snapshot["counts"]["conflicting_endpoints"] == 0

    def test_snapshot_with_orphan(self, test_db, sample_lab) -> None:
        # Create orphan reservation with no matching link
        test_db.add(models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id="orphan-id",
            node_name="ghost",
            interface_name="eth99",
        ))
        test_db.commit()

        snapshot = get_link_endpoint_reservation_health_snapshot(test_db)
        assert snapshot["counts"]["orphaned_reservations"] >= 1

    def test_snapshot_sample_limit(self, test_db, sample_lab) -> None:
        snapshot = get_link_endpoint_reservation_health_snapshot(
            test_db, sample_limit=0
        )
        # Samples should be empty/limited
        assert isinstance(snapshot["samples"]["missing_reservations"], list)


# ---------------------------------------------------------------------------
# Tests: _find_legacy_conflicts
# ---------------------------------------------------------------------------

class TestFindLegacyConflicts:
    def test_no_candidates_no_conflicts(self, test_db, sample_lab) -> None:
        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-b:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="b",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link)
        test_db.commit()

        conflicts = _find_legacy_conflicts(
            test_db, link, [("a", "eth1"), ("b", "eth1")]
        )
        assert conflicts == []

    def test_finds_overlapping_endpoint(self, test_db, sample_lab) -> None:
        link_a = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-b:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="b",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        link_b = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-c:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="c",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add_all([link_a, link_b])
        test_db.commit()

        conflicts = _find_legacy_conflicts(
            test_db, link_b, [("a", "eth1"), ("c", "eth1")]
        )
        assert "a:eth1-b:eth1" in conflicts
