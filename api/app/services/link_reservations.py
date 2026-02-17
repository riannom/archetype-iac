"""Database-backed endpoint reservations for strict 1:1 link semantics."""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.services.interface_naming import normalize_interface


def _reservation_endpoints(link_state: models.LinkState) -> list[tuple[str, str]]:
    """Return canonical endpoint tuples for a LinkState row."""
    return [
        (link_state.source_node, normalize_interface(link_state.source_interface or "")),
        (link_state.target_node, normalize_interface(link_state.target_interface or "")),
    ]


def release_link_endpoint_reservations(session: Session, link_state_id: str) -> None:
    """Delete all endpoint reservations for a LinkState."""
    session.query(models.LinkEndpointReservation).filter(
        models.LinkEndpointReservation.link_state_id == link_state_id
    ).delete(synchronize_session=False)


def get_conflicting_link_names(
    session: Session,
    lab_id: str,
    link_state_id: str,
    endpoints: list[tuple[str, str]],
) -> list[str]:
    """Resolve conflicting link names for a set of endpoint tuples."""
    conflicts: set[str] = set()
    for node_name, interface_name in endpoints:
        rows = (
            session.query(models.LinkEndpointReservation, models.LinkState)
            .join(
                models.LinkState,
                models.LinkState.id == models.LinkEndpointReservation.link_state_id,
            )
            .filter(
                models.LinkEndpointReservation.lab_id == lab_id,
                models.LinkEndpointReservation.node_name == node_name,
                models.LinkEndpointReservation.interface_name == interface_name,
                models.LinkEndpointReservation.link_state_id != link_state_id,
                models.LinkState.desired_state == "up",
            )
            .all()
        )
        for _, state in rows:
            conflicts.add(state.link_name)
    return sorted(conflicts)


def get_conflicting_link_details(
    session: Session,
    lab_id: str,
    link_state_id: str,
    endpoints: list[tuple[str, str]],
) -> list[dict[str, object]]:
    """Resolve endpoint-level conflict details for a set of endpoint tuples."""
    details: list[dict[str, object]] = []
    for node_name, interface_name in endpoints:
        rows = (
            session.query(models.LinkEndpointReservation, models.LinkState)
            .join(
                models.LinkState,
                models.LinkState.id == models.LinkEndpointReservation.link_state_id,
            )
            .filter(
                models.LinkEndpointReservation.lab_id == lab_id,
                models.LinkEndpointReservation.node_name == node_name,
                models.LinkEndpointReservation.interface_name == interface_name,
                models.LinkEndpointReservation.link_state_id != link_state_id,
                models.LinkState.desired_state == "up",
            )
            .all()
        )
        conflict_links = sorted({state.link_name for _, state in rows})
        if conflict_links:
            details.append(
                {
                    "node_name": node_name,
                    "interface_name": interface_name,
                    "conflicting_links": conflict_links,
                }
            )
    return details


def _find_legacy_conflicts(
    session: Session,
    link_state: models.LinkState,
    endpoints: list[tuple[str, str]],
) -> list[str]:
    """Fallback conflict detection for rows not yet reserved."""
    nodes = sorted({node for node, _ in endpoints})
    candidates = (
        session.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == link_state.lab_id,
            models.LinkState.id != link_state.id,
            models.LinkState.desired_state == "up",
            or_(
                models.LinkState.source_node.in_(nodes),
                models.LinkState.target_node.in_(nodes),
            ),
        )
        .all()
    )

    endpoint_set = set(endpoints)
    conflicts: set[str] = set()
    for candidate in candidates:
        candidate_endpoints = {
            (candidate.source_node, normalize_interface(candidate.source_interface or "")),
            (candidate.target_node, normalize_interface(candidate.target_interface or "")),
        }
        if endpoint_set.intersection(candidate_endpoints):
            conflicts.add(candidate.link_name)
    return sorted(conflicts)


def claim_link_endpoints(
    session: Session,
    link_state: models.LinkState,
) -> tuple[bool, list[str]]:
    """Claim both link endpoints for a desired-up LinkState.

    Returns:
        (True, []) on success
        (False, [conflicting_link_names...]) on conflict
    """
    endpoints = _reservation_endpoints(link_state)
    expected = set(endpoints)

    existing = {
        (row.node_name, row.interface_name)
        for row in session.query(models.LinkEndpointReservation).filter(
            models.LinkEndpointReservation.link_state_id == link_state.id
        )
    }
    legacy_conflicts = _find_legacy_conflicts(session, link_state, endpoints)
    if legacy_conflicts:
        return False, legacy_conflicts
    if existing == expected:
        return True, []

    try:
        with session.begin_nested():
            release_link_endpoint_reservations(session, link_state.id)
            for node_name, interface_name in endpoints:
                session.add(
                    models.LinkEndpointReservation(
                        lab_id=link_state.lab_id,
                        link_state_id=link_state.id,
                        node_name=node_name,
                        interface_name=interface_name,
                    )
                )
            session.flush()
        return True, []
    except IntegrityError:
        conflicts = get_conflicting_link_names(
            session,
            link_state.lab_id,
            link_state.id,
            endpoints,
        )
        return False, conflicts


def sync_link_endpoint_reservations(session: Session, link_state: models.LinkState) -> tuple[bool, list[str]]:
    """Ensure reservation state matches desired_state for a link."""
    if link_state.desired_state == "up":
        return claim_link_endpoints(session, link_state)
    release_link_endpoint_reservations(session, link_state.id)
    return True, []


def reconcile_link_endpoint_reservations(session: Session) -> dict[str, int]:
    """Periodic self-heal for endpoint reservations against LinkState desired state."""
    result = {
        "checked": 0,
        "claimed": 0,
        "released": 0,
        "orphans_removed": 0,
        "conflicts": 0,
    }

    orphan_reservation_ids = [
        row[0]
        for row in (
            session.query(models.LinkEndpointReservation.id)
            .outerjoin(
                models.LinkState,
                models.LinkEndpointReservation.link_state_id == models.LinkState.id,
            )
            .filter(models.LinkState.id.is_(None))
            .all()
        )
    ]
    orphan_count = 0
    if orphan_reservation_ids:
        orphan_count = (
            session.query(models.LinkEndpointReservation)
            .filter(models.LinkEndpointReservation.id.in_(orphan_reservation_ids))
            .delete(synchronize_session=False)
        )
    result["orphans_removed"] = int(orphan_count or 0)

    links = session.query(models.LinkState).all()
    for link in links:
        result["checked"] += 1
        if link.desired_state == "up":
            ok, _ = claim_link_endpoints(session, link)
            if ok:
                result["claimed"] += 1
            else:
                result["conflicts"] += 1
        else:
            released = (
                session.query(models.LinkEndpointReservation)
                .filter(models.LinkEndpointReservation.link_state_id == link.id)
                .delete(synchronize_session=False)
            )
            result["released"] += int(released or 0)

    return result


def get_link_endpoint_reservation_drift_counts(session: Session) -> dict[str, int]:
    """Return current reservation drift/conflict counts."""
    desired_up_links = (
        session.query(models.LinkState)
        .filter(models.LinkState.desired_state == "up")
        .all()
    )

    expected: set[tuple[str, str, str]] = set()
    for link in desired_up_links:
        for node_name, iface_name in _reservation_endpoints(link):
            expected.add((link.lab_id, node_name, iface_name))

    actual_rows = session.query(models.LinkEndpointReservation).all()
    actual: set[tuple[str, str, str]] = set()
    per_endpoint_counts: dict[tuple[str, str, str], int] = {}
    for row in actual_rows:
        key = (row.lab_id, row.node_name, row.interface_name)
        actual.add(key)
        per_endpoint_counts[key] = per_endpoint_counts.get(key, 0) + 1

    missing = len(expected - actual)

    up_link_ids = {link.id for link in desired_up_links}
    orphaned = sum(1 for row in actual_rows if row.link_state_id not in up_link_ids)

    conflicts = sum(1 for count in per_endpoint_counts.values() if count > 1)

    return {
        "missing": missing,
        "orphaned": orphaned,
        "conflicts": conflicts,
        "total": len(actual_rows),
    }


def get_link_endpoint_reservation_health_snapshot(
    session: Session,
    sample_limit: int = 20,
) -> dict[str, object]:
    """Return counts + sampled drift details for endpoint reservation health."""
    desired_up_links = (
        session.query(models.LinkState)
        .filter(models.LinkState.desired_state == "up")
        .all()
    )

    expected_rows: list[dict[str, str]] = []
    for link in desired_up_links:
        for node_name, iface_name in _reservation_endpoints(link):
            expected_rows.append(
                {
                    "lab_id": link.lab_id,
                    "link_state_id": link.id,
                    "link_name": link.link_name,
                    "node_name": node_name,
                    "interface_name": iface_name,
                }
            )

    reservation_rows = session.query(models.LinkEndpointReservation).all()
    actual_keys = {
        (row.lab_id, row.link_state_id, row.node_name, row.interface_name)
        for row in reservation_rows
    }

    missing_rows = [
        row
        for row in expected_rows
        if (
            row["lab_id"],
            row["link_state_id"],
            row["node_name"],
            row["interface_name"],
        )
        not in actual_keys
    ]

    orphaned_rows = (
        session.query(models.LinkEndpointReservation, models.LinkState)
        .outerjoin(
            models.LinkState,
            models.LinkEndpointReservation.link_state_id == models.LinkState.id,
        )
        .filter(
            or_(
                models.LinkState.id.is_(None),
                models.LinkState.desired_state != "up",
            )
        )
        .all()
    )

    conflict_rows = (
        session.query(
            models.LinkEndpointReservation.lab_id,
            models.LinkEndpointReservation.node_name,
            models.LinkEndpointReservation.interface_name,
        )
        .group_by(
            models.LinkEndpointReservation.lab_id,
            models.LinkEndpointReservation.node_name,
            models.LinkEndpointReservation.interface_name,
        )
        .having(func.count(models.LinkEndpointReservation.id) > 1)
        .all()
    )

    conflict_details: list[dict[str, object]] = []
    for lab_id, node_name, interface_name in conflict_rows[:sample_limit]:
        links = (
            session.query(models.LinkState.link_name)
            .join(
                models.LinkEndpointReservation,
                models.LinkEndpointReservation.link_state_id == models.LinkState.id,
            )
            .filter(
                models.LinkEndpointReservation.lab_id == lab_id,
                models.LinkEndpointReservation.node_name == node_name,
                models.LinkEndpointReservation.interface_name == interface_name,
            )
            .all()
        )
        conflict_details.append(
            {
                "lab_id": lab_id,
                "node_name": node_name,
                "interface_name": interface_name,
                "conflicting_links": sorted({name for (name,) in links}),
            }
        )

    return {
        "counts": {
            "desired_up_links": len(desired_up_links),
            "expected_reservations": len(expected_rows),
            "total_reservations": len(reservation_rows),
            "missing_reservations": len(missing_rows),
            "orphaned_reservations": len(orphaned_rows),
            "conflicting_endpoints": len(conflict_rows),
        },
        "samples": {
            "missing_reservations": missing_rows[:sample_limit],
            "orphaned_reservations": [
                {
                    "lab_id": reservation.lab_id,
                    "link_state_id": reservation.link_state_id,
                    "link_name": link.link_name if link else None,
                    "node_name": reservation.node_name,
                    "interface_name": reservation.interface_name,
                    "link_desired_state": link.desired_state if link else None,
                }
                for reservation, link in orphaned_rows[:sample_limit]
            ],
            "conflicting_endpoints": conflict_details,
        },
    }
