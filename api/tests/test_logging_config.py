"""Tests for logging configuration (logging_config.py).

This module tests:
- JSONFormatter log output format
- TextFormatter log output format
- Correlation ID context variable management
- setup_logging() function configuration
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.logging_config import (
    JSONFormatter,
    TextFormatter,
    correlation_id_var,
    generate_correlation_id,
    get_correlation_id,
    set_correlation_id,
    setup_logging,
)


class TestCorrelationId:
    """Tests for correlation ID management."""

    def test_generate_correlation_id_returns_string(self):
        """Test that generate_correlation_id returns a string."""
        correlation_id = generate_correlation_id()
        assert isinstance(correlation_id, str)
        assert len(correlation_id) > 0

    def test_generate_correlation_id_is_unique(self):
        """Test that each call generates a unique ID."""
        ids = [generate_correlation_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_generate_correlation_id_is_uuid_format(self):
        """Test that generated ID is a valid UUID format."""
        correlation_id = generate_correlation_id()
        # UUID format: 8-4-4-4-12 hex digits
        parts = correlation_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_set_and_get_correlation_id(self):
        """Test that set_correlation_id and get_correlation_id work together."""
        test_id = "test-correlation-123"
        set_correlation_id(test_id)
        assert get_correlation_id() == test_id

    def test_get_correlation_id_default_is_none(self):
        """Test that get_correlation_id returns None by default."""
        # Reset the context variable
        token = correlation_id_var.set(None)
        try:
            assert get_correlation_id() is None
        finally:
            correlation_id_var.reset(token)

    def test_correlation_id_context_isolation(self):
        """Test that correlation IDs are isolated per context."""
        import asyncio

        async def task_with_id(task_id):
            set_correlation_id(f"task-{task_id}")
            await asyncio.sleep(0.01)  # Yield to other tasks
            return get_correlation_id()

        async def run_tasks():
            tasks = [task_with_id(i) for i in range(5)]
            results = await asyncio.gather(*tasks)
            return results

        results = asyncio.run(run_tasks())
        # Each task should have its own correlation ID
        expected = [f"task-{i}" for i in range(5)]
        assert results == expected


class TestJSONFormatter:
    """Tests for JSONFormatter class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.formatter = JSONFormatter()
        self.logger = logging.getLogger("test_json_formatter")
        self.logger.setLevel(logging.DEBUG)

    def test_format_returns_valid_json(self):
        """Test that formatter returns valid JSON string."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        # Should be valid JSON
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_format_includes_required_fields(self):
        """Test that formatted output includes all required fields."""
        record = logging.LogRecord(
            name="test.module",
            level=logging.WARNING,
            pathname="test.py",
            lineno=42,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)

        assert "timestamp" in parsed
        assert "level" in parsed
        assert "logger" in parsed
        assert "message" in parsed
        assert parsed["level"] == "WARNING"
        assert parsed["logger"] == "test.module"
        assert parsed["message"] == "Warning message"

    def test_format_timestamp_is_iso8601(self):
        """Test that timestamp is in ISO8601 format."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)

        # Should be parseable as ISO8601 datetime
        timestamp = parsed["timestamp"]
        # ISO8601 format includes timezone info
        assert "T" in timestamp
        assert "+" in timestamp or "Z" in timestamp or timestamp.endswith("+00:00")

    def test_format_includes_correlation_id_when_set(self):
        """Test that correlation ID is included when set."""
        set_correlation_id("test-correlation-456")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            output = self.formatter.format(record)
            parsed = json.loads(output)

            assert "correlation_id" in parsed
            assert parsed["correlation_id"] == "test-correlation-456"
        finally:
            set_correlation_id(None)

    def test_format_excludes_correlation_id_when_not_set(self):
        """Test that correlation ID is not included when not set."""
        set_correlation_id(None)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)

        assert "correlation_id" not in parsed

    def test_format_includes_exception_info(self):
        """Test that exception info is included when present."""
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)

        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "Test error" in parsed["exception"]

    def test_format_message_with_arguments(self):
        """Test that message formatting with arguments works."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="User %s logged in from %s",
            args=("alice", "192.168.1.1"),
            exc_info=None,
        )
        output = self.formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == "User alice logged in from 192.168.1.1"

    def test_format_includes_extra_fields(self):
        """Test that extra fields from the record are included."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        # Add extra fields
        record.user_id = 123
        record.request_path = "/api/test"

        output = self.formatter.format(record)
        parsed = json.loads(output)

        assert "extra" in parsed
        assert parsed["extra"]["user_id"] == 123
        assert parsed["extra"]["request_path"] == "/api/test"

    def test_format_serializes_non_json_extras_as_string(self):
        """Test that non-JSON-serializable extras are converted to strings."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        # Add a non-serializable object
        record.complex_object = object()

        output = self.formatter.format(record)
        parsed = json.loads(output)

        # Should still be valid JSON
        assert "extra" in parsed
        assert "complex_object" in parsed["extra"]
        # Should be converted to string representation
        assert isinstance(parsed["extra"]["complex_object"], str)


class TestTextFormatter:
    """Tests for TextFormatter class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.formatter = TextFormatter()

    def test_format_returns_string(self):
        """Test that formatter returns a string."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        assert isinstance(output, str)

    def test_format_includes_timestamp(self):
        """Test that formatted output includes timestamp."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        # Should have timestamp in brackets at the start
        assert output.startswith("[")
        # Should contain date/time pattern
        assert "-" in output  # Date separators
        assert ":" in output  # Time separators

    def test_format_includes_level(self):
        """Test that formatted output includes log level."""
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        assert "WARNING" in output

    def test_format_includes_logger_name(self):
        """Test that formatted output includes logger name."""
        record = logging.LogRecord(
            name="app.module.submodule",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        assert "app.module.submodule" in output

    def test_format_includes_message(self):
        """Test that formatted output includes message."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="This is the log message",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        assert "This is the log message" in output

    def test_format_includes_correlation_id_when_set(self):
        """Test that correlation ID is included when set."""
        full_id = "abcd1234-5678-9abc-def0-123456789012"
        set_correlation_id(full_id)
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            output = self.formatter.format(record)
            # TextFormatter shows first 8 chars of correlation ID
            assert "[abcd1234]" in output
        finally:
            set_correlation_id(None)

    def test_format_excludes_correlation_id_when_not_set(self):
        """Test that no correlation ID brackets when not set."""
        set_correlation_id(None)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        output = self.formatter.format(record)
        # Should not have extra brackets for correlation ID
        # Count bracket pairs - should only have timestamp
        bracket_count = output.count("[")
        assert bracket_count == 1  # Only timestamp brackets

    def test_format_includes_exception_traceback(self):
        """Test that exception traceback is included."""
        try:
            raise RuntimeError("Test runtime error")
        except RuntimeError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = self.formatter.format(record)

        assert "RuntimeError" in output
        assert "Test runtime error" in output
        assert "Traceback" in output

    def test_format_message_with_arguments(self):
        """Test that message formatting with arguments works."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Processing %d items for user %s",
            args=(42, "bob"),
            exc_info=None,
        )
        output = self.formatter.format(record)
        assert "Processing 42 items for user bob" in output


class TestSetupLogging:
    """Tests for setup_logging() function."""

    def test_setup_logging_configures_root_logger(self):
        """Test that setup_logging configures the root logger."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "text"
            mock_settings.log_level = "DEBUG"

            setup_logging()

            root_logger = logging.getLogger()
            assert root_logger.level == logging.DEBUG

    def test_setup_logging_uses_json_formatter(self):
        """Test that setup_logging uses JSONFormatter when log_format is json."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "json"
            mock_settings.log_level = "INFO"

            setup_logging()

            root_logger = logging.getLogger()
            # Check that one of the handlers uses JSONFormatter
            json_handlers = [
                h for h in root_logger.handlers
                if isinstance(h.formatter, JSONFormatter)
            ]
            assert len(json_handlers) > 0

    def test_setup_logging_uses_text_formatter(self):
        """Test that setup_logging uses TextFormatter when log_format is text."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "text"
            mock_settings.log_level = "INFO"

            setup_logging()

            root_logger = logging.getLogger()
            text_handlers = [
                h for h in root_logger.handlers
                if isinstance(h.formatter, TextFormatter)
            ]
            assert len(text_handlers) > 0

    def test_setup_logging_removes_existing_handlers(self):
        """Test that setup_logging removes existing handlers."""
        root_logger = logging.getLogger()

        # Add some existing handlers
        existing_handler = logging.StreamHandler()
        root_logger.addHandler(existing_handler)
        initial_count = len(root_logger.handlers)

        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "text"
            mock_settings.log_level = "INFO"

            setup_logging()

            # Should have exactly one handler after setup
            assert len(root_logger.handlers) == 1
            # The existing handler should be removed
            assert existing_handler not in root_logger.handlers

    def test_setup_logging_reduces_noisy_loggers(self):
        """Test that setup_logging reduces noise from third-party libraries."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "text"
            mock_settings.log_level = "DEBUG"

            setup_logging()

            # These loggers should be set to WARNING
            assert logging.getLogger("uvicorn.access").level == logging.WARNING
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
            assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

    def test_setup_logging_with_invalid_log_level(self):
        """Test that setup_logging handles invalid log level gracefully."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "text"
            mock_settings.log_level = "INVALID_LEVEL"

            # Should not raise, should default to INFO
            setup_logging()

            root_logger = logging.getLogger()
            assert root_logger.level == logging.INFO

    def test_setup_logging_case_insensitive_format(self):
        """Test that log_format is case insensitive."""
        with patch("app.logging_config.settings") as mock_settings:
            mock_settings.log_format = "JSON"  # uppercase
            mock_settings.log_level = "INFO"

            setup_logging()

            root_logger = logging.getLogger()
            json_handlers = [
                h for h in root_logger.handlers
                if isinstance(h.formatter, JSONFormatter)
            ]
            assert len(json_handlers) > 0


class TestLoggerOutput:
    """Integration tests for actual logging output."""

    def test_json_logging_end_to_end(self, capsys):
        """Test complete JSON logging flow."""
        import sys

        # Create a logger with JSON formatter
        logger = logging.getLogger("test_e2e_json")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)

        set_correlation_id("e2e-test-id-123")
        try:
            logger.info("End to end test", extra={"custom_field": "value"})
        finally:
            set_correlation_id(None)

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())

        assert output["message"] == "End to end test"
        assert output["level"] == "INFO"
        assert output["correlation_id"] == "e2e-test-id-123"
        assert output["extra"]["custom_field"] == "value"

    def test_text_logging_end_to_end(self, capsys):
        """Test complete text logging flow."""
        import sys

        logger = logging.getLogger("test_e2e_text")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(TextFormatter())
        logger.addHandler(handler)

        set_correlation_id("text-test-abc")
        try:
            logger.warning("Text format test")
        finally:
            set_correlation_id(None)

        captured = capsys.readouterr()
        output = captured.out.strip()

        assert "WARNING" in output
        assert "Text format test" in output
        assert "[text-tes]" in output  # First 8 chars of correlation ID
