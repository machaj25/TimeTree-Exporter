"""Tests for the utils module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from timetree_exporter.event import TimeTreeEvent
from timetree_exporter.exporter import create_calendar
from timetree_exporter.formatter import ICalEventFormatter
from timetree_exporter.utils import (
    add_bounded_timezones_before_events,
    convert_timestamp_to_datetime,
    get_events_from_file,
    get_timezone_date_ranges,
    paths_to_filelist,
)


def test_get_events_from_file(temp_event_file):
    """Test getting events from a JSON file."""
    events = get_events_from_file(temp_event_file)

    assert len(events) == 2
    assert events[0]["uuid"] == "test-uuid-1"
    assert events[0]["title"] == "Test event 1"
    assert events[1]["uuid"] == "test-uuid-2"
    assert events[1]["title"] == "Test event 2"


def test_get_public_events_from_file(temp_public_event_file):
    """Test getting public events from a JSON file."""
    events = get_events_from_file(temp_public_event_file)

    assert len(events) == 2
    assert events[0]["uuid"] == "test-uuid-1"
    assert events[0]["title"] == "Public test event 1"
    assert events[1]["uuid"] == "test-uuid-2"
    assert events[1]["title"] == "Public test event 2"


def test_get_events_from_invalid_file(temp_invalid_file):
    """Test getting events from a file with invalid format."""
    events = get_events_from_file(temp_invalid_file)
    assert events is None


def test_get_events_from_nonexistent_file():
    """Test getting events from a nonexistent file."""
    events = get_events_from_file("nonexistent_file.json")
    assert events is None


def test_paths_to_filelist(temp_directory):
    """Test converting paths to a file list."""
    # Create a standalone file for the test
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file_path = temp_file.name

    try:
        # Test a mix of directory and file paths
        paths = [temp_directory, temp_file_path]
        file_list = paths_to_filelist(paths)

        # Verify the result
        assert len(file_list) == 4  # 3 files in directory + 1 temp file
        assert temp_file_path in file_list

        # Check that files inside the directory are included in the list
        for i in range(3):
            expected_file = str(Path(temp_directory) / f"file_{i}.txt")
            assert expected_file in file_list

        # Test an invalid path
        invalid_paths = ["/nonexistent/path"]
        invalid_file_list = paths_to_filelist(invalid_paths)
        assert len(invalid_file_list) == 0
    finally:
        # Clean up
        Path(temp_file_path).unlink()


def test_convert_timestamp_to_datetime():
    """Test converting timestamps to datetime objects."""
    # Test a positive timestamp (after 1970)
    positive_timestamp = 1713110000  # around 2024-04-14
    positive_dt = convert_timestamp_to_datetime(positive_timestamp, ZoneInfo("UTC"))

    assert isinstance(positive_dt, datetime)
    assert positive_dt.year == 2024
    assert positive_dt.tzinfo == ZoneInfo("UTC")

    # Test timestamps with different timezones
    taipei_dt = convert_timestamp_to_datetime(positive_timestamp, ZoneInfo("Asia/Taipei"))
    assert taipei_dt.tzinfo == ZoneInfo("Asia/Taipei")

    # Test that the timezone conversion is correct (check the offsets)
    new_york_dt = convert_timestamp_to_datetime(positive_timestamp, ZoneInfo("America/New_York"))
    assert new_york_dt.tzinfo == ZoneInfo("America/New_York")

    # Check the time difference between timezones
    # Taipei is 8 hours ahead of UTC
    utc_taipei_diff = (taipei_dt.utcoffset() - positive_dt.utcoffset()).total_seconds() / 3600
    assert utc_taipei_diff == 8.0

    # Compute the UTC to New York offset (-4 or -5 hours depending on the season)
    utc_ny_diff = (new_york_dt.utcoffset() - positive_dt.utcoffset()).total_seconds() / 3600
    # April 2024 is daylight saving time, so the offset should be -4 hours
    assert utc_ny_diff == -4.0

    # Test a negative timestamp (before 1970)
    negative_timestamp = -10000000  # around December 1969
    negative_dt = convert_timestamp_to_datetime(negative_timestamp, ZoneInfo("UTC"))

    assert isinstance(negative_dt, datetime)
    assert negative_dt.year == 1969
    assert negative_dt.tzinfo == ZoneInfo("UTC")

    # Test that the local time for a specific timestamp is correct
    # Create a known timestamp and check the corresponding local time
    timestamp_2023_01_01 = 1672531200  # 2023-01-01 00:00:00 UTC
    london_dt = convert_timestamp_to_datetime(timestamp_2023_01_01, ZoneInfo("Europe/London"))
    assert london_dt.year == 2023
    assert london_dt.month == 1
    assert london_dt.day == 1
    assert london_dt.hour == 0  # winter time, London equals UTC

    tokyo_dt = convert_timestamp_to_datetime(timestamp_2023_01_01, ZoneInfo("Asia/Tokyo"))
    assert tokyo_dt.year == 2023
    assert tokyo_dt.month == 1
    assert tokyo_dt.day == 1
    assert tokyo_dt.hour == 9  # Tokyo is 9 hours ahead of UTC


def test_get_timezone_date_ranges(normal_event_data):
    """Test collecting bounded date ranges for each referenced TZID."""
    data = normal_event_data.copy()
    taipei_time = datetime(2024, 8, 14, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
    data["start_at"] = int(taipei_time.timestamp() * 1000)
    data["end_at"] = int((taipei_time + timedelta(hours=1)).timestamp() * 1000)

    cal = create_calendar()
    cal.add_component(ICalEventFormatter(TimeTreeEvent.from_dict(data)).to_ical())

    assert get_timezone_date_ranges(cal) == {
        "Asia/Taipei": (taipei_time.date(), taipei_time.date())
    }


def test_add_bounded_timezones_warns_when_blocks_exceed_200_lines(
    monkeypatch, caplog, normal_event_data
):
    """Warn when generated VTIMEZONE blocks may trigger Google Calendar parsing bugs."""
    data = normal_event_data.copy()
    data["start_timezone"] = "America/New_York"
    data["end_timezone"] = "America/New_York"
    new_york_time = datetime(2024, 8, 14, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    data["start_at"] = int(new_york_time.timestamp() * 1000)
    data["end_at"] = int((new_york_time + timedelta(hours=1)).timestamp() * 1000)

    cal = create_calendar()
    cal.add_component(ICalEventFormatter(TimeTreeEvent.from_dict(data)).to_ical())

    class _LargeTimezone:
        name = "VTIMEZONE"

        def to_ical(self):
            return ("BEGIN:VTIMEZONE\n" + "X-LINE:1\n" * 201 + "END:VTIMEZONE\n").encode()

    monkeypatch.setattr(
        "timetree_exporter.utils.Timezone.from_tzid",
        lambda *_args, **_kwargs: _LargeTimezone(),
    )

    add_bounded_timezones_before_events(cal)

    assert "Generated VTIMEZONE blocks contain 203 lines" in caplog.text
