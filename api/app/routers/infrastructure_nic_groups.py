"""NIC group CRUD endpoints for host interface affinity."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_admin, get_current_user
from app.utils.http import raise_not_found
from app.schemas import (
    HostNicGroupOut,
    HostNicGroupCreate,
    HostNicGroupMemberOut,
    HostNicGroupMemberCreate,
    HostNicGroupsResponse,
)


router = APIRouter()


# --- NIC Group CRUD (future interface affinity) ---


@router.get("/nic-groups", response_model=HostNicGroupsResponse)
def list_nic_groups(
    host_id: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> HostNicGroupsResponse:
    """List NIC groups, optionally filtered by host."""
    query = database.query(models.HostNicGroup)
    if host_id:
        query = query.filter(models.HostNicGroup.host_id == host_id)

    groups = query.all()

    host_ids = {g.host_id for g in groups}
    hosts = database.query(models.Host).filter(models.Host.id.in_(host_ids)).all() if host_ids else []
    host_names = {h.id: h.name for h in hosts}

    group_ids = {g.id for g in groups}
    members = (
        database.query(models.HostNicGroupMember)
        .filter(models.HostNicGroupMember.nic_group_id.in_(group_ids))
        .all()
        if group_ids else []
    )
    interface_ids = {m.managed_interface_id for m in members}
    interfaces = (
        database.query(models.AgentManagedInterface)
        .filter(models.AgentManagedInterface.id.in_(interface_ids))
        .all()
        if interface_ids else []
    )
    interface_lookup = {iface.id: iface for iface in interfaces}

    members_by_group: dict[str, list[HostNicGroupMemberOut]] = {}
    for member in members:
        out = HostNicGroupMemberOut.model_validate(member)
        iface = interface_lookup.get(member.managed_interface_id)
        if iface:
            out.interface_name = iface.name
            out.interface_type = iface.interface_type
        members_by_group.setdefault(member.nic_group_id, []).append(out)

    result = []
    for group in groups:
        out = HostNicGroupOut.model_validate(group)
        out.host_name = host_names.get(group.host_id)
        out.members = members_by_group.get(group.id, [])
        result.append(out)

    return HostNicGroupsResponse(groups=result, total=len(result))


@router.post("/hosts/{host_id}/nic-groups", response_model=HostNicGroupOut)
def create_nic_group(
    host_id: str,
    request: HostNicGroupCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> HostNicGroupOut:
    """Create a NIC group on a host."""

    host = database.get(models.Host, host_id)
    if not host:
        raise_not_found("Host not found")

    existing = (
        database.query(models.HostNicGroup)
        .filter(models.HostNicGroup.host_id == host_id, models.HostNicGroup.name == request.name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"NIC group {request.name} already exists on this host")

    group = models.HostNicGroup(
        host_id=host_id,
        name=request.name,
        description=request.description,
    )
    database.add(group)
    database.commit()
    database.refresh(group)

    out = HostNicGroupOut.model_validate(group)
    out.host_name = host.name
    out.members = []
    return out


@router.post("/nic-groups/{group_id}/members", response_model=HostNicGroupMemberOut)
def add_nic_group_member(
    group_id: str,
    request: HostNicGroupMemberCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> HostNicGroupMemberOut:
    """Add a managed interface to a NIC group."""

    group = database.get(models.HostNicGroup, group_id)
    if not group:
        raise_not_found("NIC group not found")

    iface = database.get(models.AgentManagedInterface, request.managed_interface_id)
    if not iface:
        raise_not_found("Managed interface not found")

    if iface.host_id != group.host_id:
        raise HTTPException(status_code=400, detail="Managed interface belongs to a different host")

    existing = (
        database.query(models.HostNicGroupMember)
        .filter(
            models.HostNicGroupMember.nic_group_id == group_id,
            models.HostNicGroupMember.managed_interface_id == request.managed_interface_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Interface already in this NIC group")

    member = models.HostNicGroupMember(
        nic_group_id=group_id,
        managed_interface_id=request.managed_interface_id,
        role=request.role,
    )
    database.add(member)
    database.commit()
    database.refresh(member)

    out = HostNicGroupMemberOut.model_validate(member)
    out.interface_name = iface.name
    out.interface_type = iface.interface_type
    return out


@router.delete("/nic-groups/{group_id}/members/{member_id}")
def delete_nic_group_member(
    group_id: str,
    member_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Remove a member from a NIC group."""

    member = database.get(models.HostNicGroupMember, member_id)
    if not member or member.nic_group_id != group_id:
        raise_not_found("NIC group member not found")

    database.delete(member)
    database.commit()

    return {"success": True}


@router.delete("/nic-groups/{group_id}")
def delete_nic_group(
    group_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Delete a NIC group and its members."""

    group = database.get(models.HostNicGroup, group_id)
    if not group:
        raise_not_found("NIC group not found")

    database.delete(group)
    database.commit()

    return {"success": True}
