"""
Constants for timetree_exporter
"""

API_BASEURI = "https://timetreeapp.com/api/v1"
API_V2_BASEURI = "https://timetreeapp.com/api/v2"
ATTACHMENTS_BASEURI = "https://attachments.timetreeapp.com"
API_USER_AGENT = "web/2.1.0/en"

# (connect timeout, read timeout) in seconds for regular JSON API calls.
REQUEST_TIMEOUT: tuple[float, float] = (10, 30)
# Image downloads can be large, so allow a longer read timeout.
ATTACHMENT_TIMEOUT: tuple[float, float] = (10, 60)

# Automatic retry with exponential backoff for transient failures.
# TimeTree answers rate limiting with HTTP 429 and honours Retry-After.
RETRY_TOTAL = 5
RETRY_BACKOFF_FACTOR = 1.0
RETRY_STATUS_FORCELIST: tuple[int, ...] = (429, 500, 502, 503, 504)

SESSION_COOKIE_NAME = "_session_id"
