"""Helpers for parsing relative time ranges like '15m', '1h', '24h'."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable


def parse_relative_duration(
    value: str | None,
    allowed: Iterable[str] | None = None,
) -> timedelta | None:
    """Parse a relative duration like '15m', '1h', or '24h' into a timedelta.

    Args:
        value: Relative duration string.
        allowed: Optional iterable of allowed values. If provided, values not in
            the allowed set return None.

    Returns:
        timedelta if parsed, otherwise None.
    """
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    if allowed is not None and value not in set(allowed):
        return None

    import re

    match = re.match(r"^(\d+)([mhd])$", value)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)

    return None
