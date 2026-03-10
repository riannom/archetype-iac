"""Shared test factory helpers for creating model instances.

These factories consolidate the duplicated _make_* helpers found across
many test files.  Each factory creates a model instance, persists it to
the database (add + commit + refresh by default), and returns it.

Set ``flush_only=True`` to use ``session.flush()`` instead of
``session.commit()`` / ``session.refresh()`` — useful for tests that
manage their own transaction boundaries.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Internal persistence helper
# ---------------------------------------------------------------------------

def _persist(db: Session, obj, *, flush_only: bool = False):
    """Add *obj* to the session and either flush or commit+refresh."""
    db.add(obj)
    if flush_only:
        db.flush()
    else:
        db.commit()
        db.refresh(obj)
    return obj


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------

def make_host(
    db: Session,
    host_id: str | None = None,
    name: str | None = None,
    status: str = "online",
    *,
    address: str | None = None,
    capabilities: dict | str | None = None,
    version: str = "1.0.0",
    resource_usage: dict | str | None = None,
    last_heartbeat: datetime | None = None,
    heartbeat_offset: timedelta = timedelta(seconds=0),
    providers: list[str] | None = None,
    deployment_mode: str | None = None,
    is_local: bool = False,
    last_error: str | None = None,
    error_since: datetime | None = None,
    data_plane_address: str | None = None,
    image_sync_strategy: str | None = None,
    started_at: datetime | None = None,
    flush_only: bool = False,
) -> models.Host:
    """Create and persist a Host record.

    Parameters are a superset of all variants found across the test suite.
    Callers that previously used ``_make_host(test_db, ...)`` can switch to
    ``make_host(test_db, ...)`` with the same positional/keyword arguments.
    """
    hid = host_id or str(uuid4())[:8]
    resolved_name = name or hid

    # Capabilities: accept dict or pre-encoded string.
    if capabilities is None:
        caps_str = json.dumps({"providers": providers or ["docker"]})
    elif isinstance(capabilities, dict):
        caps_str = json.dumps(capabilities)
    else:
        caps_str = capabilities

    # Resource usage: accept dict or pre-encoded string.
    if resource_usage is None:
        ru_str = json.dumps({})
    elif isinstance(resource_usage, dict):
        ru_str = json.dumps(resource_usage)
    else:
        ru_str = resource_usage

    host = models.Host(
        id=hid,
        name=resolved_name,
        address=address or f"{hid}.local:8080",
        status=status,
        capabilities=caps_str,
        version=version,
        resource_usage=ru_str,
        last_heartbeat=last_heartbeat or (datetime.now(timezone.utc) - heartbeat_offset),
        deployment_mode=deployment_mode,
        last_error=last_error,
        error_since=error_since,
        data_plane_address=data_plane_address,
        image_sync_strategy=image_sync_strategy,
        started_at=started_at,
    )
    # Some test files set is_local when the model supports it.
    if is_local:
        try:
            host.is_local = is_local
        except AttributeError:
            pass
    return _persist(db, host, flush_only=flush_only)


# ---------------------------------------------------------------------------
# Lab
# ---------------------------------------------------------------------------

def make_lab(
    db: Session,
    owner: models.User | str | None = None,
    *,
    owner_id: str | None = None,
    lab_id: str | None = None,
    name: str | None = None,
    state: str = "stopped",
    provider: str = "docker",
    agent_id: str | None = None,
    workspace_path: str | None = None,
    state_updated_at: datetime | None = None,
    flush_only: bool = False,
) -> models.Lab:
    """Create and persist a Lab record.

    *owner* may be a ``models.User`` instance (uses ``.id``) or a raw
    ``owner_id`` string.
    """
    if owner_id is not None:
        resolved_owner_id = owner_id
    elif isinstance(owner, models.User):
        resolved_owner_id = owner.id
    else:
        resolved_owner_id = owner

    kwargs: dict = dict(
        name=name or f"Lab-{uuid4().hex[:8]}",
        owner_id=resolved_owner_id,
        provider=provider,
        state=state,
        workspace_path=workspace_path or "/tmp/test-lab",
        agent_id=agent_id,
    )
    if lab_id is not None:
        kwargs["id"] = lab_id
    if state_updated_at is not None:
        kwargs["state_updated_at"] = state_updated_at

    lab = models.Lab(**kwargs)
    return _persist(db, lab, flush_only=flush_only)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

def make_job(
    db: Session,
    lab: models.Lab | str | None = None,
    user: models.User | str | None = None,
    status: str = "queued",
    *,
    lab_id: str | None = None,
    job_id: str | None = None,
    action: str = "sync:lab",
    created_at: datetime | None = None,
    flush_only: bool = False,
) -> models.Job:
    """Create and persist a Job record."""
    if lab_id is not None:
        resolved_lab_id = lab_id
    elif lab is not None:
        resolved_lab_id = lab.id if isinstance(lab, models.Lab) else lab
    else:
        resolved_lab_id = None
    if user is None:
        user_id = None
    elif isinstance(user, models.User):
        user_id = user.id
    else:
        user_id = user

    kwargs: dict = dict(
        lab_id=resolved_lab_id,
        user_id=user_id,
        action=action,
        status=status,
    )
    if job_id is not None:
        kwargs["id"] = job_id
    if created_at is not None:
        kwargs["created_at"] = created_at

    job = models.Job(**kwargs)
    return _persist(db, job, flush_only=flush_only)


# ---------------------------------------------------------------------------
# Node (definition) — the ``nodes`` table
# ---------------------------------------------------------------------------

def make_node(
    db: Session,
    lab: models.Lab | str,
    gui_id: str | None = None,
    display_name: str | None = None,
    container_name: str | None = None,
    device: str = "linux",
    *,
    name: str | None = None,
    node_id: str | None = None,
    host_id: str | None = None,
    node_type: str = "device",
    image: str | None = None,
    config_json: str | None = None,
    managed_interface_id: str | None = None,
    flush_only: bool = False,
) -> models.Node:
    """Create and persist a Node definition.

    Accepts ``name=`` as an alias for ``display_name`` (and ``container_name``
    when neither is set explicitly).  When only ``gui_id`` is provided,
    ``display_name`` and ``container_name`` are derived from it so that
    multiple nodes in the same lab get distinct ``container_name`` values.
    """
    lab_id = lab.id if isinstance(lab, models.Lab) else lab

    # ``name=`` is a legacy alias used by many old _make_node helpers.
    resolved_display = display_name or name or gui_id or "R1"
    resolved_container = container_name or resolved_display

    kwargs: dict = dict(
        lab_id=lab_id,
        gui_id=gui_id or resolved_display.lower(),
        display_name=resolved_display,
        container_name=resolved_container,
        device=device,
        host_id=host_id,
        node_type=node_type,
        image=image,
        config_json=config_json,
        managed_interface_id=managed_interface_id,
    )
    if node_id is not None:
        kwargs["id"] = node_id

    node = models.Node(**kwargs)
    return _persist(db, node, flush_only=flush_only)


# ---------------------------------------------------------------------------
# NodeState
# ---------------------------------------------------------------------------

def make_node_state(
    db: Session,
    lab: models.Lab | str,
    node_id_or_name=None,
    node_name: str | None = None,
    *,
    node_id: str | None = None,
    node_definition_id: str | None = None,
    desired: str | None = None,
    actual: str | None = None,
    desired_state: str | None = None,
    actual_state: str | None = None,
    is_ready: bool = False,
    error_message: str | None = None,
    management_ip: str | None = None,
    management_ips_json: str | None = None,
    boot_started_at: datetime | None = None,
    starting_started_at: datetime | None = None,
    stopping_started_at: datetime | None = None,
    image_sync_status: str | None = None,
    enforcement_failed_at: datetime | None = None,
    updated_at: datetime | None = None,
    flush_only: bool = False,
    **extra,
) -> models.NodeState:
    """Create and persist a NodeState record.

    If *updated_at* is provided, the column is set via a raw UPDATE after
    the initial insert (to bypass any ``onupdate`` trigger on the column).

    The 3rd positional argument can be:
      - A string used as node_name (or node_id when *node_name* is also given)
      - A ``models.Node`` object (extracts ``id`` as ``node_definition_id``,
        ``container_name`` as ``node_name``)
      - ``None`` (defaults to ``"R1"``)
    """
    lab_id = lab.id if isinstance(lab, models.Lab) else lab

    # Accept both short (desired/actual) and long (desired_state/actual_state)
    # keyword forms.  The long form takes priority when both are supplied.
    resolved_desired = desired_state or desired or "stopped"
    resolved_actual = actual_state or actual or "undeployed"

    # If a Node object is passed as the 3rd positional, extract its fields.
    if isinstance(node_id_or_name, models.Node):
        node_obj = node_id_or_name
        node_definition_id = node_definition_id or node_obj.id
        node_id_or_name = node_obj.gui_id
        if node_name is None:
            node_name = node_obj.container_name

    # Flexible positional args: supports both
    #   make_node_state(db, lab, node_id, node_name, ...)
    #   make_node_state(db, lab, node_name, ...)
    if node_name is not None:
        # Called with two positional strings: (node_id, node_name)
        resolved_node_id = node_id or node_id_or_name or node_name.lower()
        resolved_node_name = node_name
    else:
        # Called with one positional string: treat as node_name
        resolved_node_name = node_id_or_name or "R1"
        resolved_node_id = node_id or resolved_node_name.lower()

    ns = models.NodeState(
        lab_id=lab_id,
        node_id=resolved_node_id,
        node_name=resolved_node_name,
        node_definition_id=node_definition_id,
        desired_state=resolved_desired,
        actual_state=resolved_actual,
        is_ready=is_ready,
        error_message=error_message,
        management_ip=management_ip,
        management_ips_json=management_ips_json,
        boot_started_at=boot_started_at,
        starting_started_at=starting_started_at,
        stopping_started_at=stopping_started_at,
        image_sync_status=image_sync_status,
        enforcement_failed_at=enforcement_failed_at,
        **extra,
    )
    _persist(db, ns, flush_only=flush_only)

    if updated_at is not None and not flush_only:
        db.execute(
            models.NodeState.__table__.update()
            .where(models.NodeState.id == ns.id)
            .values(updated_at=updated_at)
        )
        db.commit()
        db.refresh(ns)

    return ns


# ---------------------------------------------------------------------------
# NodePlacement
# ---------------------------------------------------------------------------

def make_placement(
    db: Session,
    lab: models.Lab | str,
    node_name_or_host_or_node,
    host_id_or_node_name=None,
    node_or_none=None,
    *,
    node_definition_id: str | None = None,
    status: str = "pending",
    runtime_id: str | None = None,
    flush_only: bool = False,
) -> models.NodePlacement:
    """Create and persist a NodePlacement record.

    Accepts multiple calling conventions:
      make_placement(db, lab, "node_name", "host_id")
      make_placement(db, lab, node_obj, "host_id")
      make_placement(db, lab, host_obj, node_obj)
      make_placement(db, lab, host_obj, "node_name", node_obj)
    """
    lab_id = lab.id if isinstance(lab, models.Lab) else lab

    if isinstance(node_name_or_host_or_node, models.Host):
        # Host object as 3rd arg
        resolved_host_id = node_name_or_host_or_node.id
        if isinstance(host_id_or_node_name, models.Node):
            # make_placement(db, lab, host, node)
            node = host_id_or_node_name
            resolved_node_name = node.container_name
            resolved_node_def_id = node_definition_id or node.id
        elif node_or_none is not None and isinstance(node_or_none, models.Node):
            # make_placement(db, lab, host, "node_name", node)
            resolved_node_name = host_id_or_node_name
            resolved_node_def_id = node_definition_id or node_or_none.id
        else:
            resolved_node_name = host_id_or_node_name
            resolved_node_def_id = node_definition_id
    elif isinstance(node_name_or_host_or_node, models.Node):
        # Node object as 3rd arg: make_placement(db, lab, node, host_id)
        node = node_name_or_host_or_node
        resolved_node_name = node.container_name
        resolved_host_id = host_id_or_node_name
        resolved_node_def_id = node_definition_id or node.id
    else:
        # Standard pattern: make_placement(db, lab, node_name, host_id)
        resolved_node_name = node_name_or_host_or_node
        resolved_host_id = host_id_or_node_name
        resolved_node_def_id = node_definition_id

    p = models.NodePlacement(
        lab_id=lab_id,
        node_name=resolved_node_name,
        host_id=resolved_host_id,
        node_definition_id=resolved_node_def_id,
        status=status,
        runtime_id=runtime_id,
    )
    return _persist(db, p, flush_only=flush_only)


# ---------------------------------------------------------------------------
# LinkState
# ---------------------------------------------------------------------------

def make_link_state(
    db: Session,
    lab: models.Lab | str,
    link_name_or_source_node: str | None = None,
    source_iface_pos: str | None = None,
    target_node_pos: str | None = None,
    target_iface_pos: str | None = None,
    *,
    link_name: str | None = None,
    source_node: str = "R1",
    source_interface: str = "eth1",
    target_node: str = "R2",
    target_interface: str = "eth1",
    desired: str | None = None,
    actual: str | None = None,
    desired_state: str | None = None,
    actual_state: str | None = None,
    is_cross_host: bool = False,
    source_host_id: str | None = None,
    target_host_id: str | None = None,
    vlan_tag: int | None = None,
    source_vlan_tag: int | None = None,
    target_vlan_tag: int | None = None,
    vni: int | None = None,
    link_definition_id: str | None = None,
    error_message: str | None = None,
    source_carrier_state: str | None = None,
    target_carrier_state: str | None = None,
    source_vxlan_attached: bool = False,
    target_vxlan_attached: bool = False,
    flush_only: bool = False,
) -> models.LinkState:
    """Create and persist a LinkState record.

    Accepts multiple calling conventions:
      make_link_state(db, lab, link_name="R1:eth1-R2:eth1", ...)
      make_link_state(db, lab, "R1:eth1-R2:eth1", ...)
      make_link_state(db, lab, "R1", "eth1", "R2", "eth1", ...)
    """
    lab_id = lab.id if isinstance(lab, models.Lab) else lab

    # Detect calling convention from positional args
    if source_iface_pos is not None:
        # 4 positional strings: source_node, source_iface, target_node, target_iface
        source_node = link_name_or_source_node
        source_interface = source_iface_pos
        target_node = target_node_pos
        target_interface = target_iface_pos
        resolved_link_name = link_name or f"{source_node}:{source_interface}-{target_node}:{target_interface}"
    elif link_name_or_source_node is not None:
        # Single positional string: treat as link_name
        resolved_link_name = link_name_or_source_node
    else:
        resolved_link_name = link_name or f"{source_node}:{source_interface}-{target_node}:{target_interface}"

    resolved_desired = desired_state or desired or "up"
    resolved_actual = actual_state or actual or "unknown"

    ls = models.LinkState(
        lab_id=lab_id,
        link_name=resolved_link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        desired_state=resolved_desired,
        actual_state=resolved_actual,
        is_cross_host=is_cross_host,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        vlan_tag=vlan_tag,
        source_vlan_tag=source_vlan_tag,
        target_vlan_tag=target_vlan_tag,
        vni=vni,
        link_definition_id=link_definition_id,
        error_message=error_message,
        source_carrier_state=source_carrier_state,
        target_carrier_state=target_carrier_state,
        source_vxlan_attached=source_vxlan_attached,
        target_vxlan_attached=target_vxlan_attached,
    )
    return _persist(db, ls, flush_only=flush_only)


# ---------------------------------------------------------------------------
# Link (definition) — the ``links`` table
# ---------------------------------------------------------------------------

def make_link(
    db: Session,
    lab: models.Lab | str,
    source_node_id: str,
    source_interface: str,
    target_node_id: str,
    target_interface: str,
    *,
    link_name: str | None = None,
    config_json: str | None = None,
    flush_only: bool = False,
) -> models.Link:
    """Create and persist a Link definition."""
    lab_id = lab.id if isinstance(lab, models.Lab) else lab

    link = models.Link(
        lab_id=lab_id,
        link_name=link_name or f"{source_interface}-{target_interface}",
        source_node_id=source_node_id,
        source_interface=source_interface,
        target_node_id=target_node_id,
        target_interface=target_interface,
        config_json=config_json,
    )
    return _persist(db, link, flush_only=flush_only)
