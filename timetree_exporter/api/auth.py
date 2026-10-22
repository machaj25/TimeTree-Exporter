"""
This module contains the authentication helpers for the TimeTree web API.
"""

import logging
import uuid

import requests

from timetree_exporter.api.const import (
    API_BASEURI,
    API_USER_AGENT,
    REQUEST_TIMEOUT,
    SESSION_COOKIE_NAME,
)

logger = logging.getLogger(__name__)


def _extract_error_code(response: requests.Response) -> int | None:
    """Extract API error code from a login response body."""
    try:
        body = response.json()
    except ValueError:
        return None

    if not isinstance(body, dict):
        return None

    error = body.get("error")

    return error.get("code") if isinstance(error, dict) else None


def login(email: str, password: str) -> str:
    """
    Log in to the TimeTree app and return the session ID.

    Raises :class:`AuthenticationError` (or a subclass) when the login fails or
    the server does not return a session cookie.
    """
    url = f"{API_BASEURI}/auth/email/signin"
    payload = {
        "uid": email,
        "password": password,
        "uuid": str(uuid.uuid4()).replace("-", ""),
    }
    headers = {
        "Content-Type": "application/json",
        "X-Timetreea": API_USER_AGENT,
    }

    try:
        response = requests.put(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise AuthenticationError(f"Could not reach TimeTree: {exc}") from exc

    if response.status_code != 200:
        error_code = _extract_error_code(response)
        # Keep the response body out of the normal log output: it can echo the
        # account email. It stays available at DEBUG level for troubleshooting.
        logger.error("Login failed (HTTP %s, error code %s)", response.status_code, error_code)
        logger.debug("Login response body: %s", response.text)
        if error_code == -702:
            raise InvalidCredentialsError("Wrong email or password")
        if error_code == -495:
            raise RateLimitAuthenticationError("Rate limited, please try again later")
        raise AuthenticationError("Login failed")

    session_id = response.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise AuthenticationError("Login succeeded but TimeTree returned no session cookie")
    return session_id


class AuthenticationError(Exception):
    """
    Exception raised when the user is not authorized to access the resource.
    """


class InvalidCredentialsError(AuthenticationError):
    """Exception raised when email or password is invalid."""


class RateLimitAuthenticationError(AuthenticationError):
    """Exception raised when login is rate-limited by API."""
