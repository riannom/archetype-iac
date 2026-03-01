from __future__ import annotations

from agent.providers import naming


def test_sanitize_id_removes_unsupported_characters() -> None:
    assert naming.sanitize_id("lab !@#-_ABC.123") == "lab-_ABC123"


def test_sanitize_id_respects_max_len() -> None:
    assert naming.sanitize_id("abcdefghijklmnop", max_len=6) == "abcdef"


def test_docker_container_name_uses_prefix_and_sanitized_fields() -> None:
    name = naming.docker_container_name("lab-12345678901234567890zz", "R1/eth1")
    assert name == "archetype-lab-1234567890123456-R1eth1"


def test_libvirt_domain_name_truncates_node_name() -> None:
    node = "node-name-with-more-than-thirty-characters"
    name = naming.libvirt_domain_name("lab/id", node)

    assert name.startswith("arch-labid-")
    assert len(name.split("-", 2)[-1]) == 30
