"""Shared test factory functions for creating database objects.

These replace the dozens of duplicated _make_* helpers scattered across test files.
Import and call directly — these are plain functions, not pytest fixtures.

Usage:
    from tests.factories import make_host, make_lab, make_node_state

    def test_something(test_db, test_user):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user.id)
        ns = make_node_state(test_db, lab.id, "n1", "R1")
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app import models


def make_host(
    test_db,
    *,
    host_id: str = "agent-1",
    name: str | None = None,
    address: str | None = None,
    status: str = "online",
    capabilities: dict | str | None = None,
    version: str = "1.0.0",
    resource_usage: dict | str | None = None,
    last_heartbeat: datetime | None = None,
) -> models.Host:
    """Create a Host record.

    Args:
        name: Defaults to host_id if not provided.
        address: Defaults to "{host_id}.local:8080" if not provided.
        capabilities: Dict or JSON string. Defaults to docker provider.
        resource_usage: Dict or JSON string. Defaults to reasonable test values.
    """
    if name is None:
        name = host_id.replace("-", " ").title()
    if address is None:
        address = f"{host_id}.local:8080"
    if capabilities is None:
        capabilities = {"providers": ["docker"]}
    if isinstance(capabilities, dict):
        capabilities = json.dumps(capabilities)
    if resource_usage is None:
        resource_usage = {
            "cpu_percent": 25.0,
            "memory_percent": 40.0,
            "disk_percent": 30.0,
            "disk_used_gb": 60.0,
            "disk_total_gb": 200.0,
            "containers_running": 2,
            "containers_total": 4,
            "container_details": [],
        }
    if isinstance(resource_usage, dict):
        resource_usage = json.dumps(resource_usage)
    if last_heartbeat is None:
        last_heartbeat = datetime.now(timezone.utc)

    host = models.Host(
        id=host_id,
        name=name,
        address=address,
        status=status,
        capabilities=capabilities,
        version=version,
        resource_usage=resource_usage,
        last_heartbeat=last_heartbeat,
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def make_lab(
    test_db,
    owner_id: str,
    *,
    name: str = "Test Lab",
    state: str = "stopped",
    provider: str = "docker",
    agent_id: str | None = None,
    workspace_path: str = "/tmp/test-lab",
) -> models.Lab:
    """Create a Lab record."""
    lab = models.Lab(
        name=name,
        owner_id=owner_id,
        provider=provider,
        state=state,
        workspace_path=workspace_path,
        agent_id=agent_id,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def make_job(
    test_db,
    lab_id: str,
    user_id: str,
    *,
    action: str = "sync",
    status: str = "queued",
    agent_id: str | None = None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
) -> models.Job:
    """Create a Job record."""
    kwargs: dict = {
        "lab_id": lab_id,
        "user_id": user_id,
        "action": action,
        "status": status,
    }
    if agent_id is not None:
        kwargs["agent_id"] = agent_id
    if created_at is not None:
        kwargs["created_at"] = created_at
    if started_at is not None:
        kwargs["started_at"] = started_at

    job = models.Job(**kwargs)
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def make_node_state(
    test_db,
    lab_id: str,
    node_id: str,
    node_name: str,
    *,
    desired_state: str = "stopped",
    actual_state: str = "undeployed",
    node_definition_id: str | None = None,
    management_ip: str | None = None,
    management_ips_json: str | None = None,
    error_message: str | None = None,
    is_ready: bool = False,
    **kwargs,
) -> models.NodeState:
    """Create a NodeState record.

    Accepts **kwargs for any additional NodeState model fields.
    """
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired_state,
        actual_state=actual_state,
        node_definition_id=node_definition_id,
        management_ip=management_ip,
        management_ips_json=management_ips_json,
        error_message=error_message,
        is_ready=is_ready,
        **kwargs,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


def make_link_state(
    test_db,
    lab_id: str,
    *,
    link_name: str = "R1:eth1-R2:eth1",
    source_node: str = "R1",
    source_interface: str = "eth1",
    target_node: str = "R2",
    target_interface: str = "eth1",
    desired_state: str = "up",
    actual_state: str = "unknown",
    source_host_id: str | None = None,
    target_host_id: str | None = None,
    is_cross_host: bool = False,
    vlan_tag: int | None = None,
    source_vlan_tag: int | None = None,
    target_vlan_tag: int | None = None,
    link_definition_id: str | None = None,
    error_message: str | None = None,
    **kwargs,
) -> models.LinkState:
    """Create a LinkState record.

    Accepts **kwargs for any additional LinkState model fields
    (e.g., source_vxlan_attached, target_vxlan_attached, carrier states).
    """
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        desired_state=desired_state,
        actual_state=actual_state,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        is_cross_host=is_cross_host,
        vlan_tag=vlan_tag,
        source_vlan_tag=source_vlan_tag,
        target_vlan_tag=target_vlan_tag,
        link_definition_id=link_definition_id,
        error_message=error_message,
        **kwargs,
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


def make_node(
    test_db,
    lab_id: str,
    *,
    gui_id: str = "n1",
    display_name: str = "R1",
    container_name: str | None = None,
    device: str = "linux",
    host_id: str | None = None,
    node_type: str = "device",
    kind: str | None = None,
    **kwargs,
) -> models.Node:
    """Create a Node (definition) record.

    Args:
        container_name: Defaults to display_name if not provided.
    """
    if container_name is None:
        container_name = display_name

    node = models.Node(
        lab_id=lab_id,
        gui_id=gui_id,
        display_name=display_name,
        container_name=container_name,
        device=device,
        host_id=host_id,
        node_type=node_type,
        **kwargs,
    )
    if kind is not None:
        node.kind = kind
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def make_placement(
    test_db,
    lab_id: str,
    node_name: str,
    host_id: str,
    *,
    status: str = "running",
    node_definition_id: str | None = None,
) -> models.NodePlacement:
    """Create a NodePlacement record."""
    kwargs: dict = {
        "lab_id": lab_id,
        "node_name": node_name,
        "host_id": host_id,
        "status": status,
    }
    if node_definition_id is not None:
        kwargs["node_definition_id"] = node_definition_id

    p = models.NodePlacement(**kwargs)
    test_db.add(p)
    test_db.commit()
    test_db.refresh(p)
    return p
