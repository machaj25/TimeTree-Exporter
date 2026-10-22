"""Tests for authentication API module."""

import pytest

from timetree_exporter.api.auth import (
    AuthenticationError,
    InvalidCredentialsError,
    RateLimitAuthenticationError,
    login,
)


class MockResponse:
    """Simple mock response for requests.put."""

    def __init__(self, status_code, json_body=None, text="", cookies=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.cookies = cookies or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("No JSON body")
        return self._json_body


def test_login_invalid_credentials_raises_specific_error(monkeypatch):
    """Raise a dedicated authentication error for -702."""

    def _mock_put(*args, **kwargs):
        return MockResponse(
            status_code=401,
            json_body={"error": {"code": -702}},
            text='{"error":{"code":-702}}',
        )

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with pytest.raises(InvalidCredentialsError):
        login("user@example.com", "wrong-pass")


def test_login_rate_limited_raises_specific_error(monkeypatch):
    """Raise a dedicated authentication error for -495."""

    def _mock_put(*args, **kwargs):
        return MockResponse(
            status_code=429,
            json_body={"error": {"code": -495}},
            text='{"error":{"code":-495}}',
        )

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with pytest.raises(RateLimitAuthenticationError):
        login("user@example.com", "password")


def test_login_unknown_error_code_raises_base_error(monkeypatch):
    """Keep fallback behavior for non-mapped error codes."""

    def _mock_put(*args, **kwargs):
        return MockResponse(
            status_code=401,
            json_body={"error": {"code": -999}},
            text='{"error":{"code":-999}}',
        )

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with pytest.raises(AuthenticationError):
        login("user@example.com", "password")


def test_login_without_session_cookie_raises(monkeypatch):
    """A 200 without the session cookie is still a failed login."""

    def _mock_put(*args, **kwargs):
        return MockResponse(status_code=200, json_body={}, text="{}", cookies={})

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with pytest.raises(AuthenticationError, match="no session cookie"):
        login("user@example.com", "password")


def test_login_returns_session_cookie_and_sends_timeout(monkeypatch):
    captured = {}

    def _mock_put(url, **kwargs):
        captured.update(kwargs)
        return MockResponse(status_code=200, cookies={"_session_id": "sid-123"})

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    assert login("user@example.com", "password") == "sid-123"
    assert captured["timeout"] == (10, 30)
    assert captured["json"]["uid"] == "user@example.com"


def test_login_failure_does_not_log_response_body_at_error_level(monkeypatch, caplog):
    """The body can echo the account email, so it must only appear at DEBUG level."""

    def _mock_put(*args, **kwargs):
        return MockResponse(
            status_code=401,
            json_body={"error": {"code": -702, "email": "user@example.com"}},
            text='{"error":{"code":-702,"email":"user@example.com"}}',
        )

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with caplog.at_level("ERROR"), pytest.raises(InvalidCredentialsError):
        login("user@example.com", "wrong-pass")

    assert "user@example.com" not in caplog.text
    assert "-702" in caplog.text


def test_login_network_error_is_authentication_error(monkeypatch):
    import requests

    def _mock_put(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr("timetree_exporter.api.auth.requests.put", _mock_put)

    with pytest.raises(AuthenticationError, match="Could not reach TimeTree"):
        login("user@example.com", "password")
