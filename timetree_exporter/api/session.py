"""HTTP session construction shared by the TimeTree API clients."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from timetree_exporter.api.const import (
    RETRY_BACKOFF_FACTOR,
    RETRY_STATUS_FORCELIST,
    RETRY_TOTAL,
    SESSION_COOKIE_NAME,
)


def build_retry(total: int = RETRY_TOTAL, backoff_factor: float = RETRY_BACKOFF_FACTOR) -> Retry:
    """Return the retry policy used for TimeTree requests.

    Retries are limited to idempotent GET requests and to status codes that
    signal a transient condition (rate limiting or server errors). The
    ``Retry-After`` header sent with HTTP 429 is respected, and other retries
    back off exponentially starting at ``backoff_factor`` seconds.
    """
    return Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )


def build_session(session_id: str | None = None, retry: Retry | None = None) -> requests.Session:
    """Return a ``requests.Session`` configured with retries and the TimeTree cookie."""
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry if retry is not None else build_retry())
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if session_id:
        session.cookies.set(SESSION_COOKIE_NAME, session_id)
    return session
