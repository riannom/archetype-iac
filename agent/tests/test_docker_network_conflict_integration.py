"""Integration tests for Docker network conflict handling in DockerProvider."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from agent.providers.docker import DockerProvider, LABEL_LAB_ID, LABEL_PROVIDER

try:
    import docker

    docker.from_env().ping()
    DOCKER_AVAILABLE = True
except Exception:
    DOCKER_AVAILABLE = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available"),
]


def _remove_network_quietly(docker_client, network_name: str) -> None:
    try:
        network = docker_client.networks.get(network_name)
    except docker.errors.NotFound:
        return
    try:
        network.remove()
    except Exception:
        pass


def _bridge_create_kwargs(
    provider: DockerProvider,
    network_name: str,
    lab_id: str,
    interface_name: str,
) -> dict[str, object]:
    return {
        "name": network_name,
        "driver": "bridge",
        "labels": {
            LABEL_LAB_ID: lab_id,
            LABEL_PROVIDER: provider.name,
            "archetype.type": "lab-interface",
            "archetype.interface_name": interface_name,
            "archetype.test": "network-conflict-integration",
        },
    }


def _bridge_network_matches(
    provider: DockerProvider,
    network,
    lab_id: str,
    interface_name: str,
) -> bool:
    attrs = getattr(network, "attrs", {}) or {}
    labels = attrs.get("Labels") or {}
    return (
        attrs.get("Driver") == "bridge"
        and labels.get(LABEL_LAB_ID) == lab_id
        and labels.get(LABEL_PROVIDER) == provider.name
        and labels.get("archetype.type") == "lab-interface"
        and labels.get("archetype.interface_name") == interface_name
    )


@pytest.fixture
def docker_client():
    return docker.from_env()


@pytest.fixture
def provider(docker_client, monkeypatch):
    p = DockerProvider()
    p._docker = docker_client
    p._prune_legacy_lab_networks = AsyncMock(return_value=0)

    monkeypatch.setattr(
        p,
        "_lab_network_create_kwargs",
        lambda network_name, lab_id, interface_name: _bridge_create_kwargs(
            p, network_name, lab_id, interface_name
        ),
    )
    monkeypatch.setattr(
        p,
        "_network_matches_lab_spec",
        lambda network, lab_id, interface_name: _bridge_network_matches(
            p, network, lab_id, interface_name
        ),
    )
    return p


@pytest.mark.asyncio
async def test_create_lab_networks_handles_real_409_race(provider, docker_client):
    lab_id = f"it-net-race-{uuid.uuid4().hex[:8]}"
    network_name = f"{provider._lab_network_prefix(lab_id)}-eth0"
    _remove_network_quietly(docker_client, network_name)

    original_retry = provider._retry_docker_call
    injected = False

    async def _retry_with_external_create_race(op_name, func, *args, **kwargs):
        nonlocal injected
        if op_name.startswith("create network ") and not injected:
            injected = True
            docker_client.networks.create(
                **provider._lab_network_create_kwargs(network_name, lab_id, "eth0")
            )
        return await original_retry(op_name, func, *args, **kwargs)

    provider._retry_docker_call = _retry_with_external_create_race

    try:
        result = await provider._create_lab_networks(lab_id, max_interfaces=0)
        assert result == {"eth0": network_name}

        networks = [n for n in docker_client.networks.list(names=[network_name]) if n.name == network_name]
        assert len(networks) == 1
    finally:
        _remove_network_quietly(docker_client, network_name)


@pytest.mark.asyncio
async def test_resolve_conflicting_network_rolls_back_when_recreate_fails(
    provider,
    docker_client,
    monkeypatch,
):
    lab_id = f"it-net-rollback-{uuid.uuid4().hex[:8]}"
    network_name = f"{provider._lab_network_prefix(lab_id)}-eth0"
    _remove_network_quietly(docker_client, network_name)

    stale_labels = {
        LABEL_LAB_ID: lab_id,
        LABEL_PROVIDER: "legacy-provider",
        "archetype.type": "legacy-lab-interface",
        "rollback-marker": "stale",
        "archetype.test": "network-conflict-integration",
    }
    docker_client.networks.create(
        name=network_name,
        driver="bridge",
        labels=stale_labels,
    )

    monkeypatch.setattr(provider, "_network_matches_lab_spec", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        provider,
        "_lab_network_create_kwargs",
        lambda network_name, lab_id, interface_name: {
            "name": network_name,
            "driver": "invalid-driver-for-rollback-test",
            "labels": {
                LABEL_LAB_ID: lab_id,
                LABEL_PROVIDER: provider.name,
                "archetype.type": "lab-interface",
                "archetype.interface_name": interface_name,
            },
        },
    )

    try:
        with pytest.raises(docker.errors.APIError):
            await provider._resolve_conflicting_lab_network(network_name, lab_id, "eth0")

        restored = docker_client.networks.get(network_name)
        restored.reload()
        restored_labels = restored.attrs.get("Labels") or {}
        assert restored.attrs.get("Driver") == "bridge"
        assert restored_labels.get("rollback-marker") == "stale"
    finally:
        _remove_network_quietly(docker_client, network_name)
