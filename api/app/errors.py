"""Structured error types for improved error handling and diagnosis.

This module provides standardized error categories and structured error
representations for better error messages across the application.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Categories of errors for structured error handling.

    These categories help identify the root cause of errors and provide
    actionable information for diagnosis and recovery.
    """
    # Agent connectivity errors
    AGENT_UNAVAILABLE = "agent_unavailable"  # Cannot reach agent
    AGENT_RESTART = "agent_restart"  # Agent lost state (likely restarted)
    AGENT_OFFLINE = "agent_offline"  # Agent marked offline in DB

    # Network errors
    NETWORK_TIMEOUT = "network_timeout"  # Operation timed out
    NETWORK_ERROR = "network_error"  # General network failure
    CONNECTION_REFUSED = "connection_refused"  # Connection actively refused

    # Job errors
    JOB_TIMEOUT = "job_timeout"  # Job exceeded timeout
    JOB_NOT_FOUND = "job_not_found"  # Job ID not found
    JOB_CANCELLED = "job_cancelled"  # Job was cancelled

    # Resource errors
    IMAGE_NOT_FOUND = "image_not_found"  # Docker image not found
    RESOURCE_NOT_FOUND = "resource_not_found"  # Generic resource not found

    # State errors
    RACE_CONDITION = "race_condition"  # Concurrent operation conflict
    INVALID_STATE = "invalid_state"  # Resource in unexpected state

    # Internal errors
    INTERNAL_ERROR = "internal_error"  # Unexpected internal error
    CONFIGURATION_ERROR = "configuration_error"  # Misconfiguration


@dataclass
class StructuredError:
    """Structured error representation for detailed error information.

    This class provides a standardized way to represent errors with
    context that aids in diagnosis and troubleshooting.

    Attributes:
        category: The error category for classification
        message: Human-readable error message
        details: Additional error details (e.g., exception info)
        agent_id: ID of the agent involved (if applicable)
        host_name: Name of the host involved (if applicable)
        job_id: ID of the job that failed (if applicable)
        correlation_id: Request correlation ID for tracing
        timestamp: When the error occurred
        suggestions: List of suggested actions to resolve
    """
    category: ErrorCategory
    message: str
    details: str | None = None
    agent_id: str | None = None
    host_name: str | None = None
    job_id: str | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
            "agent_id": self.agent_id,
            "host_name": self.host_name,
            "job_id": self.job_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp.isoformat(),
            "suggestions": self.suggestions,
        }

    def to_error_message(self) -> str:
        """Generate a concise error message for storage.

        Returns a string suitable for storing in error_message fields.
        """
        parts = [f"[{self.category.value}] {self.message}"]
        if self.details:
            parts.append(f"Details: {self.details}")
        if self.host_name:
            parts.append(f"Host: {self.host_name}")
        if self.suggestions:
            parts.append(f"Try: {'; '.join(self.suggestions)}")
        return " | ".join(parts)


def categorize_httpx_error(
    error: Exception,
    host_name: str | None = None,
    agent_id: str | None = None,
    job_id: str | None = None,
    correlation_id: str | None = None,
) -> StructuredError:
    """Categorize an httpx exception into a StructuredError.

    Args:
        error: The httpx exception that occurred
        host_name: Name of the target host
        agent_id: ID of the target agent
        job_id: Related job ID
        correlation_id: Request correlation ID

    Returns:
        StructuredError with appropriate category and message
    """
    import httpx

    host_desc = host_name or agent_id or "unknown host"

    if isinstance(error, httpx.TimeoutException):
        return StructuredError(
            category=ErrorCategory.NETWORK_TIMEOUT,
            message=f"Connection to {host_desc} timed out",
            details=str(error),
            agent_id=agent_id,
            host_name=host_name,
            job_id=job_id,
            correlation_id=correlation_id,
            suggestions=[
                "Check if the agent is running and healthy",
                "Verify network connectivity to the agent",
                "Consider increasing timeout settings",
            ],
        )

    if isinstance(error, httpx.ConnectError):
        return StructuredError(
            category=ErrorCategory.AGENT_UNAVAILABLE,
            message=f"Cannot connect to agent on {host_desc}",
            details=str(error),
            agent_id=agent_id,
            host_name=host_name,
            job_id=job_id,
            correlation_id=correlation_id,
            suggestions=[
                "Verify the agent is running",
                "Check firewall rules allow connections",
                "Confirm the agent address is correct",
            ],
        )

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code

        if status_code == 404:
            return StructuredError(
                category=ErrorCategory.AGENT_RESTART,
                message=f"Agent on {host_desc} lost job state - may have restarted",
                details=f"HTTP 404: {error.response.text[:200] if error.response.text else 'Not Found'}",
                agent_id=agent_id,
                host_name=host_name,
                job_id=job_id,
                correlation_id=correlation_id,
                suggestions=[
                    "Check agent logs for restart events",
                    "Retry the operation",
                    "Check controller for current job status",
                ],
            )

        if status_code == 503:
            return StructuredError(
                category=ErrorCategory.AGENT_UNAVAILABLE,
                message=f"Agent on {host_desc} is unavailable (overloaded or starting)",
                details=f"HTTP 503: {error.response.text[:200] if error.response.text else 'Service Unavailable'}",
                agent_id=agent_id,
                host_name=host_name,
                job_id=job_id,
                correlation_id=correlation_id,
                suggestions=[
                    "Wait and retry the operation",
                    "Check agent health status",
                ],
            )

        return StructuredError(
            category=ErrorCategory.NETWORK_ERROR,
            message=f"Agent on {host_desc} returned error (HTTP {status_code})",
            details=f"HTTP {status_code}: {error.response.text[:200] if error.response.text else 'Unknown error'}",
            agent_id=agent_id,
            host_name=host_name,
            job_id=job_id,
            correlation_id=correlation_id,
            suggestions=[
                "Check agent logs for details",
                "Verify the request parameters are valid",
            ],
        )

    # Generic httpx error
    return StructuredError(
        category=ErrorCategory.NETWORK_ERROR,
        message=f"Network error communicating with {host_desc}",
        details=str(error),
        agent_id=agent_id,
        host_name=host_name,
        job_id=job_id,
        correlation_id=correlation_id,
        suggestions=[
            "Check network connectivity",
            "Verify agent is healthy",
        ],
    )
