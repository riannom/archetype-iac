"""Device-specific hardware safety constraints.

This module centralizes minimum hardware requirements for known
memory-intensive virtual devices so all import/update/deploy paths
apply the same guardrails.
"""
from __future__ import annotations

import re


CAT9K_MIN_MEMORY_MB = 18432
CAT9K_MIN_CPU = 4


def _normalize_device_id(device_id: str | None) -> str:
    return (device_id or "").strip().lower().replace("_", "-")


def is_cat9k_memory_intensive(device_id: str | None) -> bool:
    """Return True for Cat9k variants known to require high memory."""
    normalized = _normalize_device_id(device_id)
    if not normalized:
        return False
    return bool(
        re.search(r"(cat9000v|cat9kv)", normalized)
        and re.search(r"(uadp|q200|cat9kv)", normalized)
    )


def minimum_hardware_for_device(device_id: str | None) -> dict[str, int] | None:
    """Return minimum hardware requirements for a device, if constrained."""
    if is_cat9k_memory_intensive(device_id):
        return {"memory": CAT9K_MIN_MEMORY_MB, "cpu": CAT9K_MIN_CPU}
    return None


def validate_minimum_hardware(device_id: str | None, memory: int | None, cpu: int | None) -> None:
    """Raise ValueError when provided hardware is below required minimums."""
    minimums = minimum_hardware_for_device(device_id)
    if not minimums:
        return

    violations: list[str] = []
    if memory is not None and memory < minimums["memory"]:
        violations.append(f"memory={memory}MB < required {minimums['memory']}MB")
    if cpu is not None and cpu < minimums["cpu"]:
        violations.append(f"cpu={cpu} < required {minimums['cpu']}")

    if violations:
        raise ValueError(
            f"Device '{device_id}' is memory intensive and cannot run below minimums: "
            + ", ".join(violations)
        )
