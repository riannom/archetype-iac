"""Unit tests for libvirt VLAN allocation collision avoidance."""

from __future__ import annotations

from types import SimpleNamespace

import agent.providers.libvirt as libvirt_provider


def _make_provider() -> libvirt_provider.LibvirtProvider:
    p = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._conn = None
    p._uri = "qemu:///system"
    return p


def test_allocate_vlans_skips_tags_in_use_on_ovs_bridge(monkeypatch):
    p = _make_provider()
    start = p.VLAN_RANGE_START

    # Simulate OVS ports already using the first three tags in range.
    def _fake_run(args, **_kwargs):
        # libvirt provider queries:
        # 1) ovs-vsctl list-ports <bridge>
        # 2) ovs-vsctl --format=csv --columns=name,tag list port
        if args[:2] == ["ovs-vsctl", "list-ports"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"p{start}\np{start + 1}\np{start + 2}\n",
                stderr="",
            )
        if args[:4] == ["ovs-vsctl", "--format=csv", "--columns=name,tag", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "name,tag\n"
                    f"p{start},{start}\n"
                    f"p{start + 1},{start + 1}\n"
                    f"p{start + 2},{start + 2}\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess.run args: {args!r}")

    monkeypatch.setattr(libvirt_provider.subprocess, "run", _fake_run)

    vlans = p._allocate_vlans("lab1", "node1", 3, workspace=None)
    assert vlans == [start + 3, start + 4, start + 5]


def test_allocate_vlans_wraps_and_still_skips_used(monkeypatch):
    p = _make_provider()
    p._next_vlan["lab1"] = p.VLAN_RANGE_END

    # Mark end of range as used, forcing wrap.
    def _fake_run(args, **_kwargs):
        if args[:2] == ["ovs-vsctl", "list-ports"]:
            return SimpleNamespace(returncode=0, stdout="pend\npstart\n", stderr="")
        if args[:4] == ["ovs-vsctl", "--format=csv", "--columns=name,tag", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"name,tag\npend,{p.VLAN_RANGE_END}\npstart,{p.VLAN_RANGE_START}\n",
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess.run args: {args!r}")

    monkeypatch.setattr(libvirt_provider.subprocess, "run", _fake_run)

    vlans = p._allocate_vlans("lab1", "node1", 2, workspace=None)
    # Should skip END and START and pick the next free.
    assert vlans[0] not in {p.VLAN_RANGE_END, p.VLAN_RANGE_START}
    assert len(set(vlans)) == 2


def test_recover_stale_network_discovers_domain_tags_even_without_file(tmp_path):
    p = _make_provider()

    class _Dom:
        def __init__(self, name: str, tags: list[int]) -> None:
            self._name = name
            self._tags = tags

        def name(self):
            return self._name

        def XMLDesc(self, *_args):
            tags_xml = "".join([f"<tag id='{t}'/>" for t in self._tags])
            return f"""<domain type='qemu'>
  <devices>
    <interface type='bridge'>
      <vlan>{tags_xml}</vlan>
    </interface>
  </devices>
</domain>"""

    class _Conn:
        def isAlive(self):  # noqa: N802
            return True

        def listAllDomains(self, *_args, **_kwargs):  # noqa: N802
            return [
                _Dom("arch-lab1-nodeA", [2007]),
                _Dom("arch-lab1-nodeB", [2008, 2009]),
                _Dom("arch-otherlab-x", [2010]),
            ]

    p._conn = _Conn()

    recovered = p._recover_stale_network("lab1", tmp_path)
    assert recovered["nodeA"] == [2007]
    assert recovered["nodeB"] == [2008, 2009]
