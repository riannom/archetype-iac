"""Tests for infrastructure NIC groups router (infrastructure_nic_groups.py)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PREFIX = "/infrastructure"


def _create_managed_interface(
    db: Session,
    host_id: str,
    name: str = "eth1",
    interface_type: str = "transport",
) -> models.AgentManagedInterface:
    """Insert an AgentManagedInterface and return it."""
    iface = models.AgentManagedInterface(
        host_id=host_id,
        name=name,
        interface_type=interface_type,
    )
    db.add(iface)
    db.commit()
    db.refresh(iface)
    return iface


def _create_nic_group(
    db: Session,
    host_id: str,
    name: str = "group-1",
    description: str | None = "test group",
) -> models.HostNicGroup:
    """Insert a HostNicGroup and return it."""
    group = models.HostNicGroup(
        host_id=host_id,
        name=name,
        description=description,
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def _create_member(
    db: Session,
    nic_group_id: str,
    managed_interface_id: str,
    role: str | None = "transport",
) -> models.HostNicGroupMember:
    """Insert a HostNicGroupMember and return it."""
    member = models.HostNicGroupMember(
        nic_group_id=nic_group_id,
        managed_interface_id=managed_interface_id,
        role=role,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


# ---------------------------------------------------------------------------
# GET /infrastructure/nic-groups
# ---------------------------------------------------------------------------


class TestListNicGroups:
    """Tests for GET /infrastructure/nic-groups."""

    def test_list_empty(self, test_client, auth_headers):
        """Returns empty list when no NIC groups exist."""
        resp = test_client.get(f"{PREFIX}/nic-groups", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["groups"] == []
        assert data["total"] == 0

    def test_list_with_data(self, test_client, auth_headers, test_db, sample_host):
        """Returns groups with members and resolved interface names."""
        group = _create_nic_group(test_db, sample_host.id, name="data-plane")
        iface = _create_managed_interface(test_db, sample_host.id, name="ens192", interface_type="transport")
        _create_member(test_db, group.id, iface.id, role="transport")

        resp = test_client.get(f"{PREFIX}/nic-groups", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        grp = data["groups"][0]
        assert grp["name"] == "data-plane"
        assert grp["host_name"] == sample_host.name
        assert len(grp["members"]) == 1
        assert grp["members"][0]["interface_name"] == "ens192"
        assert grp["members"][0]["interface_type"] == "transport"

    def test_list_filter_by_host_id(self, test_client, auth_headers, test_db, sample_host):
        """Filters groups by host_id query parameter."""
        _create_nic_group(test_db, sample_host.id, name="grp-a")

        # Query with the correct host_id — should return 1
        resp = test_client.get(
            f"{PREFIX}/nic-groups", params={"host_id": sample_host.id}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # Query with a non-existent host_id — should return 0
        resp = test_client.get(
            f"{PREFIX}/nic-groups", params={"host_id": "no-such-host"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# POST /infrastructure/hosts/{host_id}/nic-groups
# ---------------------------------------------------------------------------


class TestCreateNicGroup:
    """Tests for POST /infrastructure/hosts/{host_id}/nic-groups."""

    def test_create_success(self, test_client, admin_auth_headers, sample_host):
        """Creates a NIC group and returns it."""
        resp = test_client.post(
            f"{PREFIX}/hosts/{sample_host.id}/nic-groups",
            json={"name": "mgmt-group", "description": "Management NICs"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "mgmt-group"
        assert data["description"] == "Management NICs"
        assert data["host_id"] == sample_host.id
        assert data["host_name"] == sample_host.name
        assert data["members"] == []

    def test_create_host_not_found(self, test_client, admin_auth_headers):
        """Returns 404 when the host does not exist."""
        resp = test_client.post(
            f"{PREFIX}/hosts/nonexistent-host/nic-groups",
            json={"name": "grp"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_create_duplicate_name(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 409 when a group with the same name already exists on the host."""
        _create_nic_group(test_db, sample_host.id, name="dup-name")

        resp = test_client.post(
            f"{PREFIX}/hosts/{sample_host.id}/nic-groups",
            json={"name": "dup-name"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /infrastructure/nic-groups/{group_id}/members
# ---------------------------------------------------------------------------


class TestAddNicGroupMember:
    """Tests for POST /infrastructure/nic-groups/{group_id}/members."""

    def test_add_member_success(self, test_client, admin_auth_headers, test_db, sample_host):
        """Adds a managed interface to a NIC group."""
        group = _create_nic_group(test_db, sample_host.id)
        iface = _create_managed_interface(test_db, sample_host.id, name="eth2")

        resp = test_client.post(
            f"{PREFIX}/nic-groups/{group.id}/members",
            json={"managed_interface_id": iface.id, "role": "external"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["managed_interface_id"] == iface.id
        assert data["role"] == "external"
        assert data["interface_name"] == "eth2"

    def test_add_member_group_not_found(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 404 when NIC group does not exist."""
        iface = _create_managed_interface(test_db, sample_host.id)

        resp = test_client.post(
            f"{PREFIX}/nic-groups/no-such-group/members",
            json={"managed_interface_id": iface.id},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_add_member_interface_not_found(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 404 when the managed interface does not exist."""
        group = _create_nic_group(test_db, sample_host.id)

        resp = test_client.post(
            f"{PREFIX}/nic-groups/{group.id}/members",
            json={"managed_interface_id": "no-such-iface"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_add_member_wrong_host(self, test_client, admin_auth_headers, test_db, sample_host, multiple_hosts):
        """Returns 400 when interface belongs to a different host than the group."""
        group = _create_nic_group(test_db, sample_host.id)
        other_iface = _create_managed_interface(test_db, multiple_hosts[0].id, name="eth5")

        resp = test_client.post(
            f"{PREFIX}/nic-groups/{group.id}/members",
            json={"managed_interface_id": other_iface.id},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400

    def test_add_member_duplicate(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 409 when the interface is already in the group."""
        group = _create_nic_group(test_db, sample_host.id)
        iface = _create_managed_interface(test_db, sample_host.id)
        _create_member(test_db, group.id, iface.id)

        resp = test_client.post(
            f"{PREFIX}/nic-groups/{group.id}/members",
            json={"managed_interface_id": iface.id},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /infrastructure/nic-groups/{group_id}/members/{member_id}
# ---------------------------------------------------------------------------


class TestDeleteNicGroupMember:
    """Tests for DELETE /infrastructure/nic-groups/{group_id}/members/{member_id}."""

    def test_delete_member_success(self, test_client, admin_auth_headers, test_db, sample_host):
        """Deletes a member and returns success."""
        group = _create_nic_group(test_db, sample_host.id)
        iface = _create_managed_interface(test_db, sample_host.id)
        member = _create_member(test_db, group.id, iface.id)

        resp = test_client.delete(
            f"{PREFIX}/nic-groups/{group.id}/members/{member.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_member_not_found(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 404 when member does not exist."""
        group = _create_nic_group(test_db, sample_host.id)

        resp = test_client.delete(
            f"{PREFIX}/nic-groups/{group.id}/members/no-such-member",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_member_wrong_group(self, test_client, admin_auth_headers, test_db, sample_host):
        """Returns 404 when member exists but belongs to a different group."""
        group_a = _create_nic_group(test_db, sample_host.id, name="group-a")
        group_b = _create_nic_group(test_db, sample_host.id, name="group-b")
        iface = _create_managed_interface(test_db, sample_host.id)
        member = _create_member(test_db, group_a.id, iface.id)

        # Try deleting from group_b — member belongs to group_a
        resp = test_client.delete(
            f"{PREFIX}/nic-groups/{group_b.id}/members/{member.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /infrastructure/nic-groups/{group_id}
# ---------------------------------------------------------------------------


class TestDeleteNicGroup:
    """Tests for DELETE /infrastructure/nic-groups/{group_id}."""

    def test_delete_group_success(self, test_client, admin_auth_headers, test_db, sample_host):
        """Deletes an existing NIC group and returns success."""
        group = _create_nic_group(test_db, sample_host.id)

        resp = test_client.delete(
            f"{PREFIX}/nic-groups/{group.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_group_not_found(self, test_client, admin_auth_headers):
        """Returns 404 when group does not exist."""
        resp = test_client.delete(
            f"{PREFIX}/nic-groups/no-such-group",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404
