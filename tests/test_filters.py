"""Tests for date range filtering."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from timetree_exporter.filters import event_in_range, filter_events_by_date, parse_iso_date


def _ms(year, month, day, hour=0, tz="Asia/Taipei"):
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def _event(start, end, **extra):
    event = {
        "start_at": start,
        "end_at": end,
        "start_timezone": "Asia/Taipei",
        "end_timezone": "Asia/Taipei",
    }
    event.update(extra)
    return event


def test_parse_iso_date_accepts_iso_and_rejects_garbage():
    assert parse_iso_date(None) is None
    assert parse_iso_date("") is None
    assert parse_iso_date("2024-04-15") == date(2024, 4, 15)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_iso_date("15.4.2024")


def test_event_in_range_uses_event_timezone():
    """An event late in the evening in Taipei is still on that local date."""
    event = _event(_ms(2024, 4, 15, 23), _ms(2024, 4, 15, 23) + 3_600_000)

    assert event_in_range(event, date(2024, 4, 15), date(2024, 4, 15))
    assert not event_in_range(event, date(2024, 4, 17), None)
    assert not event_in_range(event, None, date(2024, 4, 14))


def test_event_in_range_keeps_overlapping_multi_day_event():
    event = _event(_ms(2024, 4, 10), _ms(2024, 4, 20))

    assert event_in_range(event, date(2024, 4, 15), date(2024, 4, 15))


def test_event_in_range_keeps_recurring_event_starting_before_range():
    event = _event(_ms(2024, 1, 1), _ms(2024, 1, 1, 1), recurrences=["RRULE:FREQ=WEEKLY"])

    assert event_in_range(event, date(2024, 4, 15), None)
    assert not event_in_range(event, None, date(2023, 12, 31))


def test_filter_events_by_date_without_bounds_is_identity():
    events = [_event(_ms(2024, 4, 15), _ms(2024, 4, 15, 1))]

    assert filter_events_by_date(events, None, None) == events


def test_filter_events_by_date_rejects_inverted_range():
    with pytest.raises(ValueError, match="--from"):
        filter_events_by_date([], date(2024, 5, 1), date(2024, 4, 1))


def test_filter_events_by_date_keeps_events_without_start():
    assert filter_events_by_date([{"title": "no dates"}], date(2024, 1, 1), None) == [
        {"title": "no dates"}
    ]
