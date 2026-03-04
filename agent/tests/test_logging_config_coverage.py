"""Tests for agent/logging_config.py — JSON and text formatters, setup function."""
from __future__ import annotations

import json
import logging
from unittest.mock import patch

from agent.logging_config import (
    AgentJSONFormatter,
    AgentTextFormatter,
    setup_agent_logging,
)


# ---------------------------------------------------------------------------
# AgentJSONFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_basic() -> None:
    """Basic log record produces valid JSON with expected fields."""
    formatter = AgentJSONFormatter(agent_id="test-agent-123")
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Hello %s",
        args=("world",),
        exc_info=None,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["message"] == "Hello world"
    assert data["service"] == "agent"
    assert data["agent_id"] == "test-agent-123"
    assert "timestamp" in data


def test_json_formatter_no_agent_id() -> None:
    """When agent_id is empty, it's omitted from output."""
    formatter = AgentJSONFormatter(agent_id="")
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="", lineno=0,
        msg="warn", args=(), exc_info=None,
    )

    data = json.loads(formatter.format(record))
    assert "agent_id" not in data


def test_json_formatter_with_exception() -> None:
    """Exception info is included in the output."""
    formatter = AgentJSONFormatter(agent_id="agent-1")

    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="something failed", args=(), exc_info=exc_info,
    )

    data = json.loads(formatter.format(record))
    assert "exception" in data
    assert "ValueError" in data["exception"]
    assert "test error" in data["exception"]


def test_json_formatter_non_serializable_extra() -> None:
    """Non-JSON-serializable extra fields are converted to strings."""
    formatter = AgentJSONFormatter(agent_id="agent-1")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="msg", args=(), exc_info=None,
    )

    # Add a non-serializable attribute
    record.custom_obj = object()  # type: ignore[attr-defined]

    output = formatter.format(record)
    data = json.loads(output)

    # The extra dict should contain a stringified version
    assert "extra" in data
    assert "custom_obj" in data["extra"]
    assert isinstance(data["extra"]["custom_obj"], str)


def test_json_formatter_serializable_extra() -> None:
    """JSON-serializable extra fields are preserved as-is."""
    formatter = AgentJSONFormatter(agent_id="")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="msg", args=(), exc_info=None,
    )
    record.lab_id = "lab-123"  # type: ignore[attr-defined]
    record.node_count = 5  # type: ignore[attr-defined]

    data = json.loads(formatter.format(record))
    assert data["extra"]["lab_id"] == "lab-123"
    assert data["extra"]["node_count"] == 5


# ---------------------------------------------------------------------------
# AgentTextFormatter
# ---------------------------------------------------------------------------


def test_text_formatter_basic() -> None:
    """Basic text format includes level, logger, and message."""
    formatter = AgentTextFormatter(agent_id="abcdef12345")
    record = logging.LogRecord(
        name="app.main", level=logging.INFO, pathname="", lineno=0,
        msg="starting up", args=(), exc_info=None,
    )

    output = formatter.format(record)
    assert "INFO" in output
    assert "app.main" in output
    assert "starting up" in output
    assert "[abcdef12]" in output  # first 8 chars of agent_id


def test_text_formatter_no_agent_id() -> None:
    """Empty agent_id omits the agent bracket."""
    formatter = AgentTextFormatter(agent_id="")
    record = logging.LogRecord(
        name="test", level=logging.DEBUG, pathname="", lineno=0,
        msg="debug", args=(), exc_info=None,
    )

    output = formatter.format(record)
    assert "[ ]" not in output
    assert "[]" not in output


def test_text_formatter_with_exception() -> None:
    """Exception info is appended to text output."""
    formatter = AgentTextFormatter(agent_id="")

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="error", args=(), exc_info=exc_info,
    )

    output = formatter.format(record)
    assert "RuntimeError" in output
    assert "boom" in output


# ---------------------------------------------------------------------------
# setup_agent_logging()
# ---------------------------------------------------------------------------


def test_setup_agent_logging_json_format() -> None:
    """JSON format uses AgentJSONFormatter."""
    from agent.config import settings

    with patch.object(settings, "log_format", "json"), \
         patch.object(settings, "log_level", "DEBUG"):
        setup_agent_logging(agent_id="test-agent")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert any(
        isinstance(h.formatter, AgentJSONFormatter)
        for h in root.handlers
    )


def test_setup_agent_logging_text_format() -> None:
    """Text format uses AgentTextFormatter."""
    from agent.config import settings

    with patch.object(settings, "log_format", "text"), \
         patch.object(settings, "log_level", "WARNING"):
        setup_agent_logging(agent_id="test-agent")

    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert any(
        isinstance(h.formatter, AgentTextFormatter)
        for h in root.handlers
    )


def test_setup_agent_logging_removes_existing_handlers() -> None:
    """Existing root logger handlers are cleared."""
    root = logging.getLogger()
    dummy = logging.StreamHandler()
    root.addHandler(dummy)

    from agent.config import settings

    with patch.object(settings, "log_format", "text"), \
         patch.object(settings, "log_level", "INFO"):
        setup_agent_logging(agent_id="")

    assert dummy not in root.handlers


def test_setup_agent_logging_reduces_noisy_loggers() -> None:
    """Third-party loggers are set to WARNING."""
    from agent.config import settings

    with patch.object(settings, "log_format", "json"), \
         patch.object(settings, "log_level", "DEBUG"):
        setup_agent_logging(agent_id="")

    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("docker").level == logging.WARNING
