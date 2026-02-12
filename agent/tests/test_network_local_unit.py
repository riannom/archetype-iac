from __future__ import annotations

import asyncio

import pytest

from agent.network.local import LocalNetworkManager, get_local_manager, LocalLink


@pytest.fixture(autouse=True)
def reset_singleton_manager():
    LocalNetworkManager._instance = None
    yield
    LocalNetworkManager._instance = None


@pytest.mark.asyncio
async def test_generate_veth_name_unique():
    mgr = LocalNetworkManager()
    name1 = mgr._generate_veth_name("lab1")
    name2 = mgr._generate_veth_name("lab1")
    assert name1 != name2
    assert name1.startswith("arch")


@pytest.mark.asyncio
async def test_ip_link_exists(monkeypatch):
    mgr = LocalNetworkManager()

    async def fake_run_cmd(cmd):
        return (0 if cmd[-1] == "exists" else 1), "", ""

    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)

    assert await mgr._ip_link_exists("exists") is True
    assert await mgr._ip_link_exists("missing") is False


@pytest.mark.asyncio
async def test_create_management_network_existing(monkeypatch):
    mgr = LocalNetworkManager()

    class FakeNetwork:
        def __init__(self):
            self.id = "net1"

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    class FakeDocker:
        class networks:
            @staticmethod
            def list(names=None):
                return [FakeNetwork()]

    monkeypatch.setattr(mgr, "_networks", {})
    monkeypatch.setattr(mgr, "_docker", FakeDocker())
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    network = await mgr.create_management_network("lab1")
    assert network.lab_id == "lab1"


@pytest.mark.asyncio
async def test_delete_management_network_not_tracked(monkeypatch):
    mgr = LocalNetworkManager()

    class FakeNetwork:
        def __init__(self):
            self.removed = False

        def remove(self):
            self.removed = True

    class FakeDocker:
        class networks:
            @staticmethod
            def list(names=None):
                return [FakeNetwork()]

    monkeypatch.setattr(mgr, "_docker", FakeDocker())

    assert await mgr.delete_management_network("lab1") is True


@pytest.mark.asyncio
async def test_create_link_happy_path(monkeypatch):
    mgr = LocalNetworkManager()

    async def fake_get_pid(name):
        return 123 if name == "a" else 456

    async def fake_ip_link_exists(_name):
        return False

    async def fake_run_cmd(cmd):
        return 0, "", ""

    monkeypatch.setattr(mgr, "_get_container_pid", fake_get_pid)
    monkeypatch.setattr(mgr, "_ip_link_exists", fake_ip_link_exists)
    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)

    link = await mgr.create_link(
        lab_id="lab1",
        link_id="l1",
        container_a="a",
        container_b="b",
        iface_a="eth1",
        iface_b="eth1",
    )
    assert isinstance(link, LocalLink)
    assert link.link_id == "l1"


@pytest.mark.asyncio
async def test_delete_link_missing_containers(monkeypatch):
    mgr = LocalNetworkManager()

    async def fake_get_pid(_name):
        return None

    async def fake_run_cmd(_cmd):
        return 0, "", ""

    monkeypatch.setattr(mgr, "_get_container_pid", fake_get_pid)
    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)

    link = LocalLink(
        lab_id="lab1",
        link_id="l1",
        container_a="a",
        container_b="b",
        iface_a="eth1",
        iface_b="eth1",
        veth_host_a="va",
        veth_host_b="vb",
    )

    assert await mgr.delete_link(link) is True


@pytest.mark.asyncio
async def test_provision_dummy_interfaces(monkeypatch):
    mgr = LocalNetworkManager()

    async def fake_get_pid(_name):
        return 100

    calls = []

    async def fake_run_cmd(cmd):
        calls.append(cmd)
        # Pretend interface doesn't exist, allow creation
        if cmd[-1] == "eth1":
            return 1, "", ""
        return 0, "", ""

    monkeypatch.setattr(mgr, "_get_container_pid", fake_get_pid)
    monkeypatch.setattr(mgr, "_run_cmd", fake_run_cmd)

    created = await mgr.provision_dummy_interfaces("c1", "eth", 1, 1)
    assert created == 1


def test_get_local_manager_singleton():
    mgr1 = get_local_manager()
    mgr2 = get_local_manager()
    assert mgr1 is mgr2
