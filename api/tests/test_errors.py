"""Tests for error handling utilities (errors.py).

This module tests:
- Error categorization
- Structured error creation
- httpx error categorization
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.errors import (
    ErrorCategory,
    StructuredError,
    categorize_httpx_error,
    create_structured_error,
)


class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_all_categories_defined(self):
        """All expected categories exist."""
        assert ErrorCategory.NETWORK_ERROR
        assert ErrorCategory.TIMEOUT
        assert ErrorCategory.AUTHENTICATION
        assert ErrorCategory.AUTHORIZATION
        assert ErrorCategory.NOT_FOUND
        assert ErrorCategory.VALIDATION
        assert ErrorCategory.CONFLICT
        assert ErrorCategory.SERVER_ERROR
        assert ErrorCategory.AGENT_ERROR
        assert ErrorCategory.UNKNOWN


class TestStructuredError:
    """Tests for StructuredError class."""

    def test_basic_error(self):
        """Basic error creation."""
        error = StructuredError(
            category=ErrorCategory.NETWORK_ERROR,
            message="Connection refused",
            code="CONN_REFUSED",
        )
        assert error.category == ErrorCategory.NETWORK_ERROR
        assert error.message == "Connection refused"
        assert error.code == "CONN_REFUSED"

    def test_error_with_details(self):
        """Error with additional details."""
        error = StructuredError(
            category=ErrorCategory.AGENT_ERROR,
            message="Deploy failed",
            code="DEPLOY_FAILED",
            details={"agent_id": "agent-1", "stderr": "Image not found"},
        )
        assert error.details["agent_id"] == "agent-1"
        assert error.details["stderr"] == "Image not found"

    def test_error_message_formatting(self):
        """Error converts to user-friendly message."""
        error = StructuredError(
            category=ErrorCategory.TIMEOUT,
            message="Request timed out after 30s",
            code="TIMEOUT",
        )
        msg = error.to_error_message()
        assert "timed out" in msg.lower()

    def test_error_to_dict(self):
        """Error serializes to dict."""
        error = StructuredError(
            category=ErrorCategory.NETWORK_ERROR,
            message="Test error",
            code="TEST",
            details={"key": "value"},
        )
        d = error.to_dict()
        assert d["category"] == "NETWORK_ERROR"
        assert d["message"] == "Test error"
        assert d["code"] == "TEST"
        assert d["details"] == {"key": "value"}


class TestCreateStructuredError:
    """Tests for create_structured_error function."""

    def test_create_network_error(self):
        """Create network error."""
        error = create_structured_error(
            ErrorCategory.NETWORK_ERROR,
            "Connection refused",
            host_name="agent-1",
        )
        assert error.category == ErrorCategory.NETWORK_ERROR
        assert "Connection refused" in error.message

    def test_create_timeout_error(self):
        """Create timeout error."""
        error = create_structured_error(
            ErrorCategory.TIMEOUT,
            "Request timed out",
            timeout=30,
        )
        assert error.category == ErrorCategory.TIMEOUT

    def test_create_agent_error(self):
        """Create agent-specific error."""
        error = create_structured_error(
            ErrorCategory.AGENT_ERROR,
            "Agent rejected job",
            agent_id="agent-123",
            job_id="job-456",
        )
        assert error.category == ErrorCategory.AGENT_ERROR
        assert error.details.get("agent_id") == "agent-123"


class TestCategorizeHttpxError:
    """Tests for categorize_httpx_error function."""

    def test_connect_error(self):
        """Connect errors categorized as NETWORK_ERROR."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        error = httpx.ConnectError("Connection refused", request=request)

        structured = categorize_httpx_error(error, host_name="test-agent")
        assert structured.category == ErrorCategory.NETWORK_ERROR
        assert "test-agent" in structured.message or "Connection" in structured.message

    def test_timeout_error(self):
        """Timeout errors categorized as TIMEOUT."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        error = httpx.ReadTimeout("Read timed out", request=request)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.TIMEOUT

    def test_connect_timeout(self):
        """Connect timeout categorized as TIMEOUT."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        error = httpx.ConnectTimeout("Connect timed out", request=request)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.TIMEOUT

    def test_http_401_error(self):
        """401 status categorized as AUTHENTICATION."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 401
        response.text = "Unauthorized"
        error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.AUTHENTICATION

    def test_http_403_error(self):
        """403 status categorized as AUTHORIZATION."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 403
        response.text = "Forbidden"
        error = httpx.HTTPStatusError("Forbidden", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.AUTHORIZATION

    def test_http_404_error(self):
        """404 status categorized as NOT_FOUND."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 404
        response.text = "Not Found"
        error = httpx.HTTPStatusError("Not Found", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.NOT_FOUND

    def test_http_409_error(self):
        """409 status categorized as CONFLICT."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 409
        response.text = "Conflict"
        error = httpx.HTTPStatusError("Conflict", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.CONFLICT

    def test_http_422_error(self):
        """422 status categorized as VALIDATION."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 422
        response.text = "Validation Error"
        error = httpx.HTTPStatusError("Validation", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.VALIDATION

    def test_http_500_error(self):
        """500 status categorized as SERVER_ERROR."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 500
        response.text = "Internal Server Error"
        error = httpx.HTTPStatusError("Server Error", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.SERVER_ERROR

    def test_http_503_error(self):
        """503 status categorized as SERVER_ERROR."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        response = MagicMock()
        response.status_code = 503
        response.text = "Service Unavailable"
        error = httpx.HTTPStatusError("Unavailable", request=request, response=response)

        structured = categorize_httpx_error(error)
        assert structured.category == ErrorCategory.SERVER_ERROR

    def test_error_includes_context(self):
        """Error includes provided context."""
        request = MagicMock()
        request.url = "http://localhost:8080/test"
        error = httpx.ConnectError("Connection refused", request=request)

        structured = categorize_httpx_error(
            error,
            host_name="test-agent",
            agent_id="agent-123",
            job_id="job-456",
        )

        assert structured.details.get("agent_id") == "agent-123"
        assert structured.details.get("job_id") == "job-456"


class TestErrorMessageFormatting:
    """Tests for error message formatting."""

    def test_network_error_message(self):
        """Network error message is user-friendly."""
        error = StructuredError(
            category=ErrorCategory.NETWORK_ERROR,
            message="Connection to agent 'host1' refused",
            code="CONN_REFUSED",
        )
        msg = error.to_error_message()
        # Should not expose raw technical details
        assert msg  # Non-empty

    def test_timeout_message(self):
        """Timeout message mentions waiting."""
        error = StructuredError(
            category=ErrorCategory.TIMEOUT,
            message="Request to agent timed out after 30 seconds",
            code="TIMEOUT",
        )
        msg = error.to_error_message()
        assert "timed out" in msg.lower() or "timeout" in msg.lower()

    def test_agent_error_message(self):
        """Agent error message includes context."""
        error = StructuredError(
            category=ErrorCategory.AGENT_ERROR,
            message="Deploy failed on agent",
            code="DEPLOY_FAILED",
            details={"stdout": "Starting containers...", "stderr": "Error: image not found"},
        )
        msg = error.to_error_message()
        # Should include the message
        assert "Deploy" in msg or "failed" in msg.lower()
