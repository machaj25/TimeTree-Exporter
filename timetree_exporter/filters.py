"""Event filtering helpers (date range)."""

import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def parse_iso_date(value: str | None) -> date | None:
    """Parse a ``YYYY-MM-DD`` string into a date, or return None for empty input."""
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def _local_date(timestamp_ms: int | None, timezone: str | None) -> date | None:
    """Return the calendar date of a millisecond timestamp in the given timezone."""
    if timestamp_ms is None:
        return None
    try:
        tzinfo = ZoneInfo(timezone or "UTC")
    except Exception:
        tzinfo = ZoneInfo("UTC")
    if timestamp_ms >= 0:
        return datetime.fromtimestamp(timestamp_ms / 1000, tzinfo).date()
    return (datetime.fromtimestamp(0, tzinfo) + timedelta(milliseconds=timestamp_ms)).date()


def event_in_range(
    event: dict[str, Any], date_from: date | None = None, date_to: date | None = None
) -> bool:
    """Return whether an event overlaps the inclusive ``[date_from, date_to]`` range.

    Dates are evaluated in the event's own timezone. Recurring events are kept
    whenever they start on or before ``date_to`` because their later occurrences
    may fall inside the range even if the first one does not.
    """
    if date_from is None and date_to is None:
        return True

    start = _local_date(event.get("start_at"), event.get("start_timezone"))
    if start is None:
        # Without a start we cannot place the event; keep it rather than lose data.
        return True
    end = _local_date(event.get("end_at"), event.get("end_timezone")) or start

    if date_to is not None and start > date_to:
        return False
    ends_before_range = date_from is not None and end < date_from
    return not (ends_before_range and not event.get("recurrences"))


def filter_events_by_date(
    events: Iterable[dict[str, Any]], date_from: date | None, date_to: date | None
) -> list[dict[str, Any]]:
    """Return only the events overlapping the given inclusive date range."""
    if date_from is None and date_to is None:
        return list(events)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValueError("--from must not be later than --to")
    kept = [event for event in events if event_in_range(event, date_from, date_to)]
    logger.info("Date filter kept %d events", len(kept))
    return kept
