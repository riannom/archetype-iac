"""Agent logging configuration with JSON formatting.

This module provides structured logging capabilities for the agent:
- JSON-formatted log output for easy parsing by log aggregation systems
- Configurable log levels and formats
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from agent.config import settings


class AgentJSONFormatter(logging.Formatter):
    """JSON log formatter for agent structured logging.

    Formats log records as JSON objects with consistent fields:
    - timestamp: ISO8601 formatted timestamp
    - level: Log level (INFO, WARNING, ERROR, etc.)
    - logger: Logger name
    - message: Log message
    - service: Always "agent" for identification
    - agent_id: The agent's ID (if available)
    - extra: Additional context fields

    This format is designed for easy parsing by Loki, Elasticsearch, etc.
    """

    def __init__(self, agent_id: str = ""):
        super().__init__()
        self.agent_id = agent_id

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON."""
        # Build base log entry
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "agent",
        }

        # Add agent ID
        if self.agent_id:
            log_entry["agent_id"] = self.agent_id

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields from the record
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
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)

        if extra:
            log_entry["extra"] = extra

        return json.dumps(log_entry)


class AgentTextFormatter(logging.Formatter):
    """Text log formatter for agent (development use).

    Provides a human-readable format:
    [timestamp] LEVEL [agent_id] logger: message
    """

    def __init__(self, agent_id: str = ""):
        super().__init__()
        self.agent_id = agent_id

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as text."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        agent_part = f" [{self.agent_id[:8]}]" if self.agent_id else ""

        message = f"[{timestamp}] {record.levelname:8}{agent_part} {record.name}: {record.getMessage()}"

        if record.exc_info:
            message += f"\n{self.formatException(record.exc_info)}"

        return message


def setup_agent_logging(agent_id: str = "") -> None:
    """Configure agent logging based on settings.

    Sets up the root logger with either JSON or text formatting
    based on the log_format setting.

    Args:
        agent_id: The agent's ID for inclusion in log entries
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)

    if settings.log_format.lower() == "json":
        handler.setFormatter(AgentJSONFormatter(agent_id))
    else:
        handler.setFormatter(AgentTextFormatter(agent_id))

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
    logging.getLogger("docker").setLevel(logging.WARNING)
