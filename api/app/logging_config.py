"""Centralized logging configuration with JSON formatting and correlation IDs.

This module provides structured logging capabilities:
- JSON-formatted log output for easy parsing by log aggregation systems
- Correlation ID support for request tracing across components
- Configurable log levels and formats
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.config import settings

# Context variable for correlation ID (request-scoped)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Get the current correlation ID from context."""
    return correlation_id_var.get()


def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID in context."""
    correlation_id_var.set(correlation_id)


def generate_correlation_id() -> str:
    """Generate a new correlation ID."""
    return str(uuid4())


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging.

    Formats log records as JSON objects with consistent fields:
    - timestamp: ISO8601 formatted timestamp
    - level: Log level (INFO, WARNING, ERROR, etc.)
    - logger: Logger name
    - message: Log message
    - correlation_id: Request correlation ID (if available)
    - extra: Additional context fields

    This format is designed for easy parsing by Loki, Elasticsearch, etc.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON."""
        # Build base log entry
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if available
        correlation_id = get_correlation_id()
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields from the record
        # Exclude standard LogRecord attributes
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "taskName", "message",
        }

        extra = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                try:
                    # Ensure the value is JSON serializable
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)

        if extra:
            log_entry["extra"] = extra

        return json.dumps(log_entry)


class TextFormatter(logging.Formatter):
    """Text log formatter with correlation ID support.

    Provides a human-readable format for development use:
    [timestamp] LEVEL [correlation_id] logger: message
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as text."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        correlation_id = get_correlation_id()
        correlation_part = f" [{correlation_id[:8]}]" if correlation_id else ""

        message = f"[{timestamp}] {record.levelname:8}{correlation_part} {record.name}: {record.getMessage()}"

        if record.exc_info:
            message += f"\n{self.formatException(record.exc_info)}"

        return message


def setup_logging() -> None:
    """Configure application logging based on settings.

    Sets up the root logger with either JSON or text formatting
    based on the log_format setting. All existing handlers are
    removed and replaced with the configured handler.
    """
    # Determine log level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Create handler with appropriate formatter
    handler = logging.StreamHandler(sys.stdout)

    if settings.log_format.lower() == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for existing_handler in root_logger.handlers[:]:
        root_logger.removeHandler(existing_handler)

    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: format={settings.log_format}, level={settings.log_level}")
