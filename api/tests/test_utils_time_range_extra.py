from __future__ import annotations

from datetime import timedelta

from app.utils.time_range import parse_relative_duration


def test_parse_relative_duration_strips_whitespace():
    assert parse_relative_duration(" 5m ") == timedelta(minutes=5)


def test_parse_relative_duration_invalid_unit():
    assert parse_relative_duration("5w") is None
