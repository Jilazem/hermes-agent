"""Tests for Chromium net::ERR_* page-load error classification in browser_navigate.

Covers:
- _classify_page_load_error() helper for all mapped error codes
- Unknown net::ERR_* codes get generic chromium_net_error classification
- Non-net-error strings return None (passthrough)
- browser_navigate() surfaces structured error_code + hint on nav failure
"""

import json

import pytest

from tools import browser_tool
from tools.browser_tool import _classify_page_load_error


# ---------------------------------------------------------------------------
# Unit tests for _classify_page_load_error
# ---------------------------------------------------------------------------


class TestClassifyPageLoadError:

    def test_err_name_not_resolved(self):
        result = _classify_page_load_error("net::ERR_NAME_NOT_RESOLVED")
        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "dns_resolution_failed"
        assert "hostname" in result["hint"].lower() or "domain" in result["hint"].lower()

    def test_err_connection_refused(self):
        result = _classify_page_load_error("net::ERR_CONNECTION_REFUSED")
        assert result is not None
        assert result["error_code"] == "connection_refused"
        assert "refused" in result["hint"].lower() or "port" in result["hint"].lower()

    def test_err_connection_timed_out(self):
        result = _classify_page_load_error("net::ERR_CONNECTION_TIMED_OUT")
        assert result is not None
        assert result["error_code"] == "connection_timeout"

    def test_err_ssl_protocol_error(self):
        result = _classify_page_load_error("net::ERR_SSL_PROTOCOL_ERROR")
        assert result is not None
        assert result["error_code"] == "ssl_error"

    def test_err_cert_date_invalid(self):
        result = _classify_page_load_error("net::ERR_CERT_DATE_INVALID")
        assert result is not None
        assert result["error_code"] == "ssl_cert_expired"

    def test_err_too_many_redirects(self):
        result = _classify_page_load_error("net::ERR_TOO_MANY_REDIRECTS")
        assert result is not None
        assert result["error_code"] == "redirect_loop"

    def test_err_invalid_url(self):
        result = _classify_page_load_error("net::ERR_INVALID_URL")
        assert result is not None
        assert result["error_code"] == "invalid_url"

    def test_err_internet_disconnected(self):
        result = _classify_page_load_error("net::ERR_INTERNET_DISCONNECTED")
        assert result is not None
        assert result["error_code"] == "no_internet"

    def test_err_aborted(self):
        result = _classify_page_load_error("net::ERR_ABORTED")
        assert result is not None
        assert result["error_code"] == "navigation_aborted"

    def test_err_empty_response(self):
        result = _classify_page_load_error("net::ERR_EMPTY_RESPONSE")
        assert result is not None
        assert result["error_code"] == "empty_response"

    def test_unknown_net_error_gets_generic_classification(self):
        result = _classify_page_load_error("net::ERR_SOME_FUTURE_ERROR")
        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "chromium_net_error"
        assert "ERR_SOME_FUTURE_ERROR" in result["hint"]

    def test_case_insensitive_matching(self):
        result = _classify_page_load_error("net::err_name_not_resolved")
        assert result is not None
        assert result["error_code"] == "dns_resolution_failed"

    def test_error_embedded_in_longer_message(self):
        result = _classify_page_load_error(
            "Page load failed at https://example.com: net::ERR_NAME_NOT_RESOLVED"
        )
        assert result is not None
        assert result["error_code"] == "dns_resolution_failed"

    def test_none_input_returns_none(self):
        assert _classify_page_load_error("") is None
        assert _classify_page_load_error(None) is None  # type: ignore[arg-type]

    def test_non_net_error_returns_none(self):
        assert _classify_page_load_error("Navigation failed") is None
        assert _classify_page_load_error("Timeout") is None
        assert _classify_page_load_error("403 Forbidden") is None

    def test_classified_result_preserves_original_error_string(self):
        original = "net::ERR_NAME_NOT_RESOLVED"
        result = _classify_page_load_error(original)
        assert result is not None
        assert result["error"] == original

    def test_classified_result_has_hint_field(self):
        result = _classify_page_load_error("net::ERR_CONNECTION_REFUSED")
        assert result is not None
        assert "hint" in result
        assert len(result["hint"]) > 10


# ---------------------------------------------------------------------------
# Integration: browser_navigate surfaces structured errors on nav failure
# ---------------------------------------------------------------------------


def _make_nav_fail_patches(monkeypatch, error_msg: str):
    """Patch browser_navigate to reach the nav-failure branch with *error_msg*."""
    monkeypatch.setattr(browser_tool, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(browser_tool, "_is_local_backend", lambda: True)
    monkeypatch.setattr(browser_tool, "_allow_private_urls", lambda: True)
    monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(browser_tool, "_is_always_blocked_url", lambda url: False)
    monkeypatch.setattr(
        browser_tool,
        "_get_session_info",
        lambda task_id: {
            "session_name": f"s_{task_id}",
            "bb_session_id": None,
            "cdp_url": None,
            "features": {"local": True},
            "_first_nav": False,
        },
    )
    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda *a, **kw: {"success": False, "error": error_msg},
    )


class TestBrowserNavigatePageLoadErrors:

    def test_err_name_not_resolved_returns_structured_response(self, monkeypatch):
        _make_nav_fail_patches(monkeypatch, "net::ERR_NAME_NOT_RESOLVED")
        result = json.loads(
            browser_tool.browser_navigate("https://nonexistent.example.invalid")
        )
        assert result["success"] is False
        assert result["error_code"] == "dns_resolution_failed"
        assert "hint" in result

    def test_err_connection_refused_returns_structured_response(self, monkeypatch):
        _make_nav_fail_patches(monkeypatch, "net::ERR_CONNECTION_REFUSED")
        result = json.loads(
            browser_tool.browser_navigate("http://localhost:9999/")
        )
        assert result["success"] is False
        assert result["error_code"] == "connection_refused"

    def test_err_ssl_returns_structured_response(self, monkeypatch):
        _make_nav_fail_patches(monkeypatch, "net::ERR_SSL_PROTOCOL_ERROR")
        result = json.loads(
            browser_tool.browser_navigate("https://self-signed.example.com")
        )
        assert result["success"] is False
        assert result["error_code"] == "ssl_error"

    def test_generic_nav_failure_passes_through_without_error_code(self, monkeypatch):
        _make_nav_fail_patches(monkeypatch, "Command failed with code 1")
        result = json.loads(
            browser_tool.browser_navigate("https://example.com")
        )
        assert result["success"] is False
        assert "error_code" not in result
        assert result["error"] == "Command failed with code 1"

    def test_unknown_net_error_gets_generic_error_code(self, monkeypatch):
        _make_nav_fail_patches(monkeypatch, "net::ERR_UNKNOWN_FUTURE_CODE")
        result = json.loads(
            browser_tool.browser_navigate("https://example.com")
        )
        assert result["success"] is False
        assert result["error_code"] == "chromium_net_error"
        assert "ERR_UNKNOWN_FUTURE_CODE" in result["hint"]
