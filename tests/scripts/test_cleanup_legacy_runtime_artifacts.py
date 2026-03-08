from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from unittest.mock import MagicMock


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "cleanup_legacy_runtime_artifacts.py"
    spec = importlib.util.spec_from_file_location("cleanup_legacy_runtime_artifacts", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_classify_ovs_ports_flags_missing_metadata_keys():
    module = _load_module()

    legacy = module._classify_ovs_ports(
        [
            {
                "port_name": "vh-good",
                "external_ids": {
                    "archetype.lab_id": "lab1",
                    "archetype.endpoint_id": "ep1",
                    "archetype.network_id": "net1",
                    "archetype.interface_name": "eth1",
                },
            },
            {
                "port_name": "vh-legacy",
                "external_ids": {
                    "archetype.lab_id": "lab1",
                    "archetype.endpoint_id": "ep2",
                },
            },
            {
                "port_name": "ens5",
                "external_ids": {},
            },
        ]
    )

    assert legacy == [
        {
            "port_name": "vh-legacy",
            "missing_keys": ["archetype.network_id", "archetype.interface_name"],
            "external_ids": {
                "archetype.lab_id": "lab1",
                "archetype.endpoint_id": "ep2",
            },
        }
    ]


def test_classify_libvirt_domains_flags_missing_identity_fields():
    module = _load_module()

    legacy = module._classify_libvirt_domains(
        [
            {"domain_name": "good", "metadata": {"lab_id": "lab1", "node_name": "r1"}, "active": False},
            {"domain_name": "legacy", "metadata": {"lab_id": "lab1"}, "active": True},
        ]
    )

    assert legacy == [
        {
            "domain_name": "legacy",
            "missing_keys": ["node_name"],
            "metadata": {"lab_id": "lab1"},
            "active": True,
        }
    ]


def test_build_report_dry_run_collects_without_apply():
    module = _load_module()
    ovs_ports = [{"port_name": "vh-legacy", "missing_keys": ["archetype.lab_id"], "external_ids": {}}]
    domains = [{"domain_name": "legacy", "missing_keys": ["node_name"], "metadata": {}, "active": False}]
    module.collect_legacy_ovs_ports = MagicMock(return_value=ovs_ports)
    module.collect_legacy_libvirt_domains = MagicMock(return_value=domains)
    module.apply_ovs_cleanup = MagicMock()
    module.apply_libvirt_cleanup = MagicMock()

    report = module.build_report(
        bridge_name="arch-ovs",
        libvirt_uri="qemu:///system",
        include_ovs=True,
        include_libvirt=True,
        apply=False,
    )

    assert report["ovs"]["legacy_ports"] == ovs_ports
    assert report["libvirt"]["legacy_domains"] == domains
    module.apply_ovs_cleanup.assert_not_called()
    module.apply_libvirt_cleanup.assert_not_called()


def test_build_report_apply_removes_detected_resources():
    module = _load_module()
    ovs_ports = [{"port_name": "vh-legacy", "missing_keys": ["archetype.lab_id"], "external_ids": {}}]
    domains = [{"domain_name": "legacy", "missing_keys": ["node_name"], "metadata": {}, "active": True}]
    module.collect_legacy_ovs_ports = MagicMock(return_value=ovs_ports)
    module.collect_legacy_libvirt_domains = MagicMock(return_value=domains)
    module.apply_ovs_cleanup = MagicMock(return_value=["vh-legacy"])
    module.apply_libvirt_cleanup = MagicMock(return_value=["legacy"])

    report = module.build_report(
        bridge_name="arch-ovs",
        libvirt_uri="qemu:///system",
        include_ovs=True,
        include_libvirt=True,
        apply=True,
    )

    assert report["ovs"]["removed_ports"] == ["vh-legacy"]
    assert report["libvirt"]["removed_domains"] == ["legacy"]
    module.apply_ovs_cleanup.assert_called_once_with("arch-ovs", ovs_ports)
    module.apply_libvirt_cleanup.assert_called_once_with("qemu:///system", domains)
