from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent.network.cleanup as cleanup
import agent.network.local as local
import agent.network.interface_config as interface_config
import agent.network.backends.base as backends_base  # noqa: F401


def test_cleanup_pattern_checks() -> None:
    manager = cleanup.NetworkCleanupManager()
    assert manager._is_archetype_veth("arch1234abcd")
    assert manager._is_archetype_veth("vh123")
    assert manager._is_archetype_bridge("abr-12")
    assert manager._is_archetype_vxlan("vxlan100")


def test_cleanup_stats_to_dict() -> None:
    stats = cleanup.CleanupStats(veths_found=1, veths_orphaned=1, veths_deleted=1)
    result = stats.to_dict()
    assert result["veths_deleted"] == 1


@pytest.mark.asyncio
async def test_local_generate_veth_name() -> None:
    manager = local.LocalNetworkManager()
    name = manager._generate_veth_name("lab")
    assert name.startswith(local.VETH_PREFIX)
    assert len(name) <= 15


@pytest.mark.asyncio
async def test_local_check_subnet_conflict(monkeypatch) -> None:
    manager = local.LocalNetworkManager()

    class FakeNetwork:
        def __init__(self, name, subnet):
            self.name = name
            self.attrs = {"IPAM": {"Config": [{"Subnet": subnet}]}}

    fake_docker = SimpleNamespace(networks=SimpleNamespace(list=lambda: [
        FakeNetwork("net-a", "10.0.0.0/24"),
        FakeNetwork("net-b", "192.168.0.0/24"),
    ]))

    manager._docker = fake_docker

    conflict = await manager._check_subnet_conflict("10.0.0.0/16")
    assert conflict == "net-a"


def test_interface_config_is_in_container_env(monkeypatch) -> None:
    monkeypatch.setenv("container", "1")
    assert interface_config._is_in_container() is True


def test_interface_config_host_write_and_read(tmp_path) -> None:
    path = tmp_path / "test.txt"
    ok, error = interface_config._host_write_file(str(path), "hello")
    assert ok
    assert error is None

    content = interface_config._host_read_file(str(path))
    assert content == "hello"
