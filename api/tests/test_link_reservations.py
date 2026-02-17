from __future__ import annotations

from app import models
from app.services.link_reservations import (
    reconcile_link_endpoint_reservations,
    get_link_endpoint_reservation_drift_counts,
)


def test_reconcile_link_endpoint_reservations_heals_missing_and_stale(test_db, sample_lab) -> None:
    up_link = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        desired_state="up",
        actual_state="unknown",
    )
    down_link = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r3:eth1-r4:eth1",
        source_node="r3",
        source_interface="eth1",
        target_node="r4",
        target_interface="eth1",
        desired_state="down",
        actual_state="down",
    )
    test_db.add_all([up_link, down_link])
    test_db.commit()

    # Stale reservation on down link (should be released by reconcile)
    test_db.add(
        models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id=down_link.id,
            node_name="r3",
            interface_name="eth1",
        )
    )
    # Orphan reservation row (no matching LinkState)
    test_db.add(
        models.LinkEndpointReservation(
            lab_id=sample_lab.id,
            link_state_id="orphan-link-state-id",
            node_name="ghost",
            interface_name="eth9",
        )
    )
    test_db.commit()

    before = get_link_endpoint_reservation_drift_counts(test_db)
    assert before["missing"] >= 1
    assert before["orphaned"] >= 1

    result = reconcile_link_endpoint_reservations(test_db)
    test_db.commit()

    assert result["claimed"] >= 1
    assert result["released"] >= 1
    assert result["orphans_removed"] >= 1
    assert result["conflicts"] == 0

    up_reservations = (
        test_db.query(models.LinkEndpointReservation)
        .filter(models.LinkEndpointReservation.link_state_id == up_link.id)
        .count()
    )
    down_reservations = (
        test_db.query(models.LinkEndpointReservation)
        .filter(models.LinkEndpointReservation.link_state_id == down_link.id)
        .count()
    )
    assert up_reservations == 2
    assert down_reservations == 0

    after = get_link_endpoint_reservation_drift_counts(test_db)
    assert after["missing"] == 0
    assert after["orphaned"] == 0
    assert after["conflicts"] == 0


def test_reconcile_link_endpoint_reservations_reports_conflicts(test_db, sample_lab) -> None:
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
    test_db.add_all([link_a, link_b])
    test_db.commit()

    result = reconcile_link_endpoint_reservations(test_db)
    test_db.commit()

    assert result["checked"] == 2
    assert result["conflicts"] >= 1
    drift = get_link_endpoint_reservation_drift_counts(test_db)
    assert drift["missing"] >= 1
