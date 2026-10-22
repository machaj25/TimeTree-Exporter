"""Tests for the shared HTTP session configuration."""

from requests.adapters import HTTPAdapter

from timetree_exporter.api.const import RETRY_STATUS_FORCELIST, SESSION_COOKIE_NAME
from timetree_exporter.api.session import build_retry, build_session


def test_build_session_sets_cookie_and_mounts_retrying_adapter():
    """Sessions should carry the TimeTree cookie and retry transient failures."""
    session = build_session("session-123")

    assert session.cookies.get(SESSION_COOKIE_NAME) == "session-123"
    adapter = session.get_adapter("https://timetreeapp.com/api/v1/calendars")
    assert isinstance(adapter, HTTPAdapter)
    retry = adapter.max_retries
    assert retry.total == 5
    assert set(RETRY_STATUS_FORCELIST) <= set(retry.status_forcelist)
    assert 429 in retry.status_forcelist
    assert retry.respect_retry_after_header is True


def test_build_session_without_session_id_has_no_cookie():
    """Public calendar sessions should not send an empty cookie."""
    session = build_session(None)

    assert session.cookies.get(SESSION_COOKIE_NAME) is None


def test_build_retry_only_retries_safe_methods():
    """Login (PUT) must never be retried automatically."""
    retry = build_retry()

    assert "GET" in retry.allowed_methods
    assert "PUT" not in retry.allowed_methods
    assert "POST" not in retry.allowed_methods
    assert retry.raise_on_status is False
