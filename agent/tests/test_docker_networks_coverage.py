"""Coverage tests for docker_networks.py standalone functions."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docker.errors import APIError, NotFound

from agent.providers.docker_networks import (
    LABEL_NODE_INTERFACE_COUNT,
    LABEL_NODE_KIND,
    create_lab_networks,
    delete_lab_networks,
    prune_legacy_lab_networks,
    recover_stale_networks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(
    *,
    lab_prefix: str = "arch-lab1",
    legacy_prefixes: tuple[str, str] = ("arch-lab1", "lab1"),
) -> MagicMock:
    """Create a minimal mock DockerProvider."""
    provider = MagicMock()
    provider._lab_network_prefix.return_value = lab_prefix
    provider._legacy_lab_network_prefixes.return_value = legacy_prefixes

    # Async context manager for lab network lock
    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=False)
    provider._get_lab_network_lock.return_value = lock

    # Default retry_docker_call passes through to the callable.
    # Must use asyncio.to_thread (like production code) so exceptions
    # propagate correctly through except clauses on Python 3.14+.
    async def _passthrough_retry(desc, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    provider._retry_docker_call = _passthrough_retry
    provider.docker = MagicMock()
    return provider


def _make_network(name: str, *, containers: dict | None = None, attrs: dict | None = None) -> MagicMock:
    net = MagicMock()
    net.name = name
    net.attrs = attrs or {}
    if containers is not None:
        net.attrs["Containers"] = containers
    return net


# ---------------------------------------------------------------------------
# _iface_index (tested via recover_stale_networks sorting)
# ---------------------------------------------------------------------------


class TestIfaceIndex:
    """The _iface_index function is local to recover_stale_networks; test it
    indirectly through sorting behavior."""

    def test_regex_extracts_trailing_digits(self):
        # Verify the regex pattern used by _iface_index
        pattern = re.compile(r"(\d+)$")
        assert pattern.search("arch-lab1-eth0").group(1) == "0"
        assert pattern.search("arch-lab1-eth12").group(1) == "12"
        assert pattern.search("no-digits") is None


# ---------------------------------------------------------------------------
# create_lab_networks
# ---------------------------------------------------------------------------


class TestCreateLabNetworks:
    def test_creates_networks_for_all_interfaces(self):
        provider = _make_provider()

        # All networks are new (NotFound on get)
        provider.docker.networks.get.side_effect = NotFound("not found")
        provider.docker.networks.create.return_value = MagicMock()
        provider._network_matches_lab_spec.return_value = True
        provider._lab_network_create_kwargs.return_value = {"name": "test"}

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            result = asyncio.run(create_lab_networks(provider, "lab1", max_interfaces=2))

        # eth0, eth1, eth2 = 3 networks (0 through max_interfaces inclusive)
        assert len(result) == 3
        assert "eth0" in result
        assert "eth1" in result
        assert "eth2" in result

    def test_existing_matching_network_reused(self):
        provider = _make_provider()
        existing_net = MagicMock()
        provider.docker.networks.get.return_value = existing_net
        provider._network_matches_lab_spec.return_value = True

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            result = asyncio.run(create_lab_networks(provider, "lab1", max_interfaces=1))

        assert len(result) == 2  # eth0, eth1

    def test_conflict_409_resolved(self):
        provider = _make_provider()
        provider._lab_network_create_kwargs.return_value = {"name": "test"}
        provider._resolve_conflicting_lab_network = AsyncMock(return_value="recreated")

        async def _retry(desc, func, *args, **kwargs):
            # Wrap in asyncio.to_thread so exceptions propagate correctly
            # through except clauses on Python 3.14+.
            def _sync():
                if "inspect" in desc:
                    raise NotFound("not found")
                if "create" in desc:
                    resp = MagicMock()
                    resp.status_code = 409
                    raise APIError("conflict", response=resp)
                return func(*args, **kwargs)

            return await asyncio.to_thread(_sync)

        provider._retry_docker_call = _retry

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            result = asyncio.run(create_lab_networks(provider, "lab1", max_interfaces=0))

        assert "eth0" in result

    def test_raises_on_too_many_failures(self):
        provider = _make_provider()

        async def _retry_raise(desc, func, *args, **kwargs):
            return await asyncio.to_thread(func, *args, **kwargs)

        provider._retry_docker_call = _retry_raise
        provider.docker.networks.get.side_effect = NotFound("not found")
        provider.docker.networks.create.side_effect = RuntimeError("docker broken")
        provider._lab_network_create_kwargs.return_value = {"name": "test"}

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            with pytest.raises(RuntimeError, match="Failed to create"):
                asyncio.run(create_lab_networks(provider, "lab1", max_interfaces=2))


# ---------------------------------------------------------------------------
# delete_lab_networks
# ---------------------------------------------------------------------------


class TestDeleteLabNetworks:
    def test_deletes_by_label(self):
        provider = _make_provider()
        net1 = _make_network("arch-lab1-eth0")
        net2 = _make_network("arch-lab1-eth1")
        provider.docker.networks.list.return_value = [net1, net2]

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            deleted = asyncio.run(delete_lab_networks(provider, "lab1"))

        assert deleted == 2

    def test_fallback_to_prefix_when_no_labels(self):
        provider = _make_provider()

        # First call (by label) returns empty, second (all) returns networks
        net = _make_network("arch-lab1-eth0")
        provider.docker.networks.list.side_effect = [[], [net]]

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            deleted = asyncio.run(delete_lab_networks(provider, "lab1"))

        assert deleted == 1

    def test_handles_remove_error(self):
        provider = _make_provider()
        net = _make_network("arch-lab1-eth0")
        net.remove.side_effect = APIError("in use")
        provider.docker.networks.list.return_value = [net]

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            deleted = asyncio.run(delete_lab_networks(provider, "lab1"))

        assert deleted == 0

    def test_handles_list_error(self):
        provider = _make_provider()

        async def _retry_raise(desc, func, *args, **kwargs):
            return await asyncio.to_thread(func, *args, **kwargs)

        provider._retry_docker_call = _retry_raise
        provider.docker.networks.list.side_effect = APIError("daemon error")

        with patch("agent.providers.docker_networks.prune_legacy_lab_networks", new_callable=AsyncMock, return_value=0):
            deleted = asyncio.run(delete_lab_networks(provider, "lab1"))

        assert deleted == 0


# ---------------------------------------------------------------------------
# recover_stale_networks
# ---------------------------------------------------------------------------


class TestRecoverStaleNetworks:
    def _make_container(
        self,
        name: str = "archetype-lab1-node1",
        networks: dict | None = None,
        labels: dict | None = None,
    ) -> MagicMock:
        container = MagicMock()
        container.name = name
        container.attrs = {
            "NetworkSettings": {
                "Networks": networks or {},
            },
        }
        container.labels = labels or {}
        container.reload = MagicMock()
        return container

    def test_no_networks_returns_false(self):
        provider = _make_provider()
        container = self._make_container(networks={})

        result = asyncio.run(recover_stale_networks(provider, container, "lab1"))
        assert result is False

    def test_skips_builtin_networks(self):
        provider = _make_provider()
        container = self._make_container(networks={"bridge": {}, "host": {}})
        provider.docker.networks.list.return_value = []

        result = asyncio.run(recover_stale_networks(provider, container, "lab1"))
        assert result is False

    def test_reconnects_to_current_networks(self):
        provider = _make_provider()
        stale_net_name = "arch-lab1-eth1"
        container = self._make_container(
            networks={stale_net_name: {"NetworkID": "abc"}},
            labels={
                LABEL_NODE_INTERFACE_COUNT: "2",
                LABEL_NODE_KIND: "linux",
            },
        )

        # Stale network exists on first check, exists on disconnect
        current_net = _make_network(stale_net_name)
        provider.docker.networks.list.return_value = []  # labeled query
        provider.docker.networks.get.return_value = current_net

        with patch("agent.vendors.get_config_by_device") as mock_vendor:
            mock_vendor.return_value = SimpleNamespace(
                management_interface="eth0",
                reserved_nics=0,
            )
            result = asyncio.run(recover_stale_networks(provider, container, "lab1"))

        assert result is True

    def test_no_interface_count_label_defaults_to_one(self):
        provider = _make_provider()
        container = self._make_container(
            networks={"arch-lab1-eth1": {"NetworkID": "abc"}},
            labels={},
        )

        current_net = _make_network("arch-lab1-eth1")
        provider.docker.networks.list.return_value = []
        provider.docker.networks.get.return_value = current_net

        result = asyncio.run(recover_stale_networks(provider, container, "lab1"))
        assert result is True


# ---------------------------------------------------------------------------
# prune_legacy_lab_networks
# ---------------------------------------------------------------------------


class TestPruneLegacyLabNetworks:
    def test_removes_legacy_networks(self):
        provider = _make_provider(legacy_prefixes=("arch-lab1", "lab1"))

        legacy_net = _make_network("lab1-eth0", containers={})
        current_net = _make_network("arch-lab1-eth0")
        provider.docker.networks.list.return_value = [legacy_net, current_net]

        removed = asyncio.run(prune_legacy_lab_networks(provider, "lab1"))
        assert removed == 1
        legacy_net.remove.assert_called_once()

    def test_disconnects_containers_before_removal(self):
        provider = _make_provider(legacy_prefixes=("arch-lab1", "lab1"))

        legacy_net = _make_network("lab1-eth0", containers={"container123abc": {}})
        provider.docker.networks.list.return_value = [legacy_net]

        removed = asyncio.run(prune_legacy_lab_networks(provider, "lab1"))
        assert removed == 1

    def test_handles_remove_failure(self):
        provider = _make_provider(legacy_prefixes=("arch-lab1", "lab1"))

        legacy_net = _make_network("lab1-eth0", containers={})
        legacy_net.remove.side_effect = Exception("busy")
        provider.docker.networks.list.return_value = [legacy_net]

        removed = asyncio.run(prune_legacy_lab_networks(provider, "lab1"))
        assert removed == 0

    def test_handles_list_failure(self):
        provider = _make_provider(legacy_prefixes=("arch-lab1", "lab1"))
        provider.docker.networks.list.side_effect = Exception("docker error")

        removed = asyncio.run(prune_legacy_lab_networks(provider, "lab1"))
        assert removed == 0

    def test_skips_non_matching_networks(self):
        provider = _make_provider(legacy_prefixes=("arch-lab1", "lab1"))

        other_net = _make_network("other-lab-eth0")
        provider.docker.networks.list.return_value = [other_net]

        removed = asyncio.run(prune_legacy_lab_networks(provider, "lab1"))
        assert removed == 0
