from __future__ import annotations

import asyncio

import pytest

from app.utils.timeouts import with_timeout


@pytest.mark.asyncio
async def test_with_timeout_logs_warning_on_timeout(monkeypatch):
    warnings = []

    class Logger:
        def warning(self, msg):
            warnings.append(msg)

    monkeypatch.setattr("app.utils.timeouts.logger", Logger())

    with pytest.raises(asyncio.TimeoutError):
        await with_timeout(asyncio.sleep(0.02), timeout=0.001, description="db call")

    assert warnings
    assert "db call" in warnings[0]
