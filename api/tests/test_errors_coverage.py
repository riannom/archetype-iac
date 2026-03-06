"""Tests for api/app/errors.py — ErrorCategory, StructuredError, categorize_httpx_error."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from app.errors import ErrorCategory, StructuredError, categorize_httpx_error  # noqa: E402


# ---------------------------------------------------------------------------
# ErrorCategory
# ---------------------------------------------------------------------------

class TestErrorCategory:
    def test_enum_values_are_strings(self):
        """Every ErrorCategory member value should be a plain string."""
        for member in ErrorCategory:
            assert isinstance(member.value, str)
            assert isinstance(member, str)  # str mixin

    def test_expected_members_present(self):
        names = {m.name for m in ErrorCategory}
        expected = {
            "AGENT_UNAVAILABLE", "AGENT_RESTART", "AGENT_OFFLINE",
            "NETWORK_TIMEOUT", "NETWORK_ERROR", "CONNECTION_REFUSED",
            "JOB_TIMEOUT", "JOB_NOT_FOUND", "JOB_CANCELLED",
            "IMAGE_NOT_FOUND", "RESOURCE_NOT_FOUND",
            "RACE_CONDITION", "INVALID_STATE",
            "INTERNAL_ERROR", "CONFIGURATION_ERROR",
        }
        assert expected.issubset(names)


# ---------------------------------------------------------------------------
# StructuredError
# ---------------------------------------------------------------------------

class TestStructuredError:
    def _make(self, **overrides):
        defaults = dict(
            category=ErrorCategory.NETWORK_ERROR,
            message="something broke",
        )
        defaults.update(overrides)
        return StructuredError(**defaults)

    # -- to_dict -----------------------------------------------------------

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        expected_keys = {
            "category", "message", "details", "agent_id",
            "host_name", "job_id", "correlation_id",
            "timestamp", "suggestions",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_category_is_string_value(self):
        d = self._make(category=ErrorCategory.AGENT_RESTART).to_dict()
        assert d["category"] == "agent_restart"

    def test_to_dict_timestamp_iso_format(self):
        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        d = self._make(timestamp=ts).to_dict()
        # Should round-trip via fromisoformat
        parsed = datetime.fromisoformat(d["timestamp"])
        assert parsed == ts

    # -- to_error_message --------------------------------------------------

    def test_to_error_message_all_fields(self):
        err = self._make(
            category=ErrorCategory.AGENT_UNAVAILABLE,
            message="cannot reach host",
            details="conn refused",
            host_name="host-1",
            suggestions=["restart agent", "check firewall"],
        )
        msg = err.to_error_message()
        assert "[agent_unavailable] cannot reach host" in msg
        assert "Details: conn refused" in msg
        assert "Host: host-1" in msg
        assert "Try: restart agent; check firewall" in msg

    def test_to_error_message_without_optional_fields(self):
        err = self._make(
            category=ErrorCategory.INTERNAL_ERROR,
            message="oops",
            details=None,
            host_name=None,
            suggestions=[],
        )
        msg = err.to_error_message()
        assert msg == "[internal_error] oops"
        assert "Details:" not in msg
        assert "Host:" not in msg
        assert "Try:" not in msg

    def test_default_timestamp_is_utc(self):
        err = self._make()
        assert err.timestamp.tzinfo is not None
        assert err.timestamp.tzinfo == timezone.utc

    def test_default_suggestions_empty_list(self):
        err = self._make()
        assert err.suggestions == []


# ---------------------------------------------------------------------------
# categorize_httpx_error
# ---------------------------------------------------------------------------

def _mock_response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class TestCategorizeHttpxError:
    def test_timeout_exception(self):
        err = httpx.TimeoutException("timed out")
        result = categorize_httpx_error(err, host_name="h1")
        assert result.category == ErrorCategory.NETWORK_TIMEOUT
        assert "h1" in result.message

    def test_connect_error(self):
        err = httpx.ConnectError("refused")
        result = categorize_httpx_error(err, agent_id="a1")
        assert result.category == ErrorCategory.AGENT_UNAVAILABLE
        assert "a1" in result.message

    def test_http_status_404(self):
        resp = _mock_response(404, "Not Found")
        err = httpx.HTTPStatusError("not found", request=MagicMock(), response=resp)
        result = categorize_httpx_error(err, host_name="box")
        assert result.category == ErrorCategory.AGENT_RESTART
        assert "404" in result.details

    def test_http_status_503(self):
        resp = _mock_response(503, "Service Unavailable")
        err = httpx.HTTPStatusError("unavailable", request=MagicMock(), response=resp)
        result = categorize_httpx_error(err)
        assert result.category == ErrorCategory.AGENT_UNAVAILABLE
        assert "503" in result.details

    def test_http_status_other(self):
        resp = _mock_response(500, "Internal Server Error")
        err = httpx.HTTPStatusError("server error", request=MagicMock(), response=resp)
        result = categorize_httpx_error(err, host_name="srv")
        assert result.category == ErrorCategory.NETWORK_ERROR
        assert "500" in result.details

    def test_generic_httpx_error(self):
        err = httpx.RequestError("something weird")
        result = categorize_httpx_error(err, host_name="h2")
        assert result.category == ErrorCategory.NETWORK_ERROR
        assert "h2" in result.message

    def test_suggestions_populated_timeout(self):
        err = httpx.TimeoutException("t/o")
        result = categorize_httpx_error(err)
        assert len(result.suggestions) > 0

    def test_suggestions_populated_connect(self):
        err = httpx.ConnectError("refused")
        result = categorize_httpx_error(err)
        assert len(result.suggestions) > 0

    def test_optional_ids_propagated(self):
        err = httpx.TimeoutException("t/o")
        result = categorize_httpx_error(
            err,
            host_name="host-x",
            agent_id="agent-y",
            job_id="job-z",
            correlation_id="corr-w",
        )
        assert result.host_name == "host-x"
        assert result.agent_id == "agent-y"
        assert result.job_id == "job-z"
        assert result.correlation_id == "corr-w"
