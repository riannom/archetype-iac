from __future__ import annotations

import pytest

from app.services import device_service as device_constraints


@pytest.mark.parametrize(
    ("device_id", "expected"),
    [
        ("cat9kv-uadp", True),
        ("cat9000v-q200", True),
        ("CAT9KV", True),
        ("cat9kv_uadp", True),
        ("ceos", False),
        ("iosv", False),
        (None, False),
        ("", False),
    ],
)
def test_is_cat9k_memory_intensive(device_id: str | None, expected: bool):
    assert device_constraints.is_cat9k_memory_intensive(device_id) is expected


def test_minimum_hardware_for_cat9k():
    minimums = device_constraints.minimum_hardware_for_device("cat9kv-uadp")
    assert minimums == {
        "memory": device_constraints.CAT9K_MIN_MEMORY_MB,
        "cpu": device_constraints.CAT9K_MIN_CPU,
    }


def test_minimum_hardware_for_non_cat9k_is_none():
    assert device_constraints.minimum_hardware_for_device("ceos") is None


def test_validate_minimum_hardware_accepts_boundary_values():
    device_constraints.validate_minimum_hardware(
        "cat9000v-q200",
        memory=device_constraints.CAT9K_MIN_MEMORY_MB,
        cpu=device_constraints.CAT9K_MIN_CPU,
    )


def test_validate_minimum_hardware_raises_on_memory_violation():
    with pytest.raises(ValueError) as exc:
        device_constraints.validate_minimum_hardware(
            "cat9kv-uadp",
            memory=device_constraints.CAT9K_MIN_MEMORY_MB - 1,
            cpu=device_constraints.CAT9K_MIN_CPU,
        )

    assert "memory=" in str(exc.value)
    assert "required" in str(exc.value)


def test_validate_minimum_hardware_raises_on_cpu_violation():
    with pytest.raises(ValueError) as exc:
        device_constraints.validate_minimum_hardware(
            "cat9kv-uadp",
            memory=device_constraints.CAT9K_MIN_MEMORY_MB,
            cpu=device_constraints.CAT9K_MIN_CPU - 1,
        )

    assert "cpu=" in str(exc.value)
    assert "required" in str(exc.value)


def test_validate_minimum_hardware_skips_non_constrained_devices():
    device_constraints.validate_minimum_hardware("ceos", memory=256, cpu=1)
