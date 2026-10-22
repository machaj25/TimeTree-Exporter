"""End-to-end style tests for the CLI entry point in __main__."""

import json

import pytest
from requests.exceptions import HTTPError

from timetree_exporter import __main__ as entry
from timetree_exporter.api.auth import InvalidCredentialsError
from timetree_exporter.api.calendar import EventPage


class _FakeApi:
    """In-memory stand-in for TimeTreeCalendar."""

    def __init__(self, calendars, events_by_calendar, pages_by_calendar=None, labels=None):
        self.calendars = calendars
        self.events_by_calendar = events_by_calendar
        self.pages_by_calendar = pages_by_calendar or {}
        self.labels = labels or {}
        self.closed = False
        self.since_calls = []

    def get_metadata(self):
        return self.calendars

    def get_labels(self, _calendar_id):
        return self.labels

    def get_events(self, calendar_id, _calendar_name=None, _calendar_users=None, **_kwargs):
        return list(self.events_by_calendar.get(calendar_id, []))

    def get_events_since(self, calendar_id, since, **_kwargs):
        self.since_calls.append((calendar_id, since))
        pages = self.pages_by_calendar[calendar_id]
        return pages.pop(0) if pages else EventPage()

    def download_image(self, *_args, **_kwargs):  # pragma: no cover - not used here
        raise AssertionError("not expected")

    def close(self):
        self.closed = True


def _event(uuid, title, start_ms=1713120000000, **extra):
    event = {
        "uuid": uuid,
        "title": title,
        "created_at": 1713110000000,
        "updated_at": 1713110100000,
        "note": "",
        "location": "",
        "location_lat": None,
        "location_lon": None,
        "url": "",
        "start_at": start_ms,
        "start_timezone": "Asia/Taipei",
        "end_at": start_ms + 3_600_000,
        "end_timezone": "Asia/Taipei",
        "all_day": False,
        "alerts": [],
        "recurrences": [],
        "parent_id": None,
        "recurring_uuid": None,
        "type": 0,
        "category": 1,
    }
    event.update(extra)
    return event


def _calendar(cal_id, name, code, users=None):
    return {
        "id": cal_id,
        "name": name,
        "alias_code": code,
        "deactivated_at": None,
        "calendar_users": users or [],
    }


@pytest.fixture
def fake_api(monkeypatch):
    api = _FakeApi(
        calendars=[
            _calendar(1, "Family", "fam", users=[{"user_id": 10, "name": "Alice"}]),
            _calendar(2, "Work", "work"),
            {**_calendar(3, "Old", "old"), "deactivated_at": 123},
        ],
        events_by_calendar={
            1: [_event("f1", "Dinner", attendees=[10, 11])],
            2: [_event("w1", "Standup")],
        },
    )
    monkeypatch.setattr(entry, "authenticate", lambda *_args, **_kwargs: api)
    return api


def test_main_exports_single_calendar_by_code(fake_api, tmp_path):
    output = tmp_path / "out.ics"

    code = entry.main(["--session-id", "s", "-c", "fam", "-o", str(output)])

    assert code == entry.EXIT_OK
    assert "SUMMARY:Dinner" in output.read_text(encoding="utf-8")
    assert "ATTENDEE" not in output.read_text(encoding="utf-8")
    assert fake_api.closed is True


def test_main_exports_multiple_calendars_to_separate_files(fake_api, tmp_path):
    output = tmp_path / "out.ics"

    code = entry.main(["--session-id", "s", "-c", "fam,work", "-o", str(output)])

    assert code == entry.EXIT_OK
    assert not output.exists()
    assert "SUMMARY:Dinner" in (tmp_path / "out_Family.ics").read_text(encoding="utf-8")
    assert "SUMMARY:Standup" in (tmp_path / "out_Work.ics").read_text(encoding="utf-8")


def test_main_interactive_selection_supports_all(fake_api, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "all")
    output = tmp_path / "out.ics"

    code = entry.main(["--session-id", "s", "-o", str(output)])

    assert code == entry.EXIT_OK
    assert (tmp_path / "out_Family.ics").exists()
    assert (tmp_path / "out_Work.ics").exists()
    # The deactivated calendar is never offered.
    assert not (tmp_path / "out_Old.ics").exists()


def test_main_unknown_code_falls_back_to_prompt(fake_api, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    output = tmp_path / "out.ics"

    code = entry.main(["--session-id", "s", "-c", "nope", "-o", str(output)])

    assert code == entry.EXIT_OK
    assert "SUMMARY:Standup" in output.read_text(encoding="utf-8")
    assert "nope" in caplog.text


def test_main_invalid_interactive_answer_is_usage_error(fake_api, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")

    code = entry.main(["--session-id", "s", "-o", str(tmp_path / "out.ics")])

    assert code == entry.EXIT_USAGE_ERROR


def test_main_includes_attendees_when_requested(fake_api, tmp_path):
    output = tmp_path / "out.ics"

    entry.main(["--session-id", "s", "-c", "fam", "--include-attendees", "-o", str(output)])

    serialized = output.read_bytes().decode("utf-8").replace("\r\n ", "")
    assert "ATTENDEE;" in serialized
    assert "CN=Alice" in serialized
    assert "urn:x-timetree:user:10" in serialized
    assert "urn:x-timetree:user:11" in serialized.replace("\r\n ", "")


def test_main_date_filter(fake_api, tmp_path):
    output = tmp_path / "out.ics"

    entry.main(["--session-id", "s", "-c", "fam", "--to", "2024-01-01", "-o", str(output)])

    assert "SUMMARY:Dinner" not in output.read_text(encoding="utf-8")


def test_main_rejects_inverted_date_range(fake_api, tmp_path):
    code = entry.main(
        ["--session-id", "s", "-c", "fam", "--from", "2024-02-01", "--to", "2024-01-01"]
    )

    assert code == entry.EXIT_USAGE_ERROR


def test_main_sync_state_fetches_only_changes_on_second_run(monkeypatch, tmp_path):
    api = _FakeApi(
        calendars=[_calendar(1, "Family", "fam")],
        events_by_calendar={},
        pages_by_calendar={
            1: [
                EventPage(events=[_event("a", "First"), _event("b", "Second")], since=100),
                EventPage(events=[_event("b", "Second renamed")], deleted_uuids=["a"], since=200),
            ]
        },
    )
    monkeypatch.setattr(entry, "authenticate", lambda *_args, **_kwargs: api)
    output = tmp_path / "out.ics"
    state = tmp_path / "state.json"
    argv = ["--session-id", "s", "-c", "fam", "-o", str(output), "--sync-state", str(state)]

    assert entry.main(argv) == entry.EXIT_OK
    first = output.read_text(encoding="utf-8")
    assert "SUMMARY:First" in first and "SUMMARY:Second" in first

    assert entry.main(argv) == entry.EXIT_OK
    second = output.read_text(encoding="utf-8")
    assert "SUMMARY:First" not in second
    assert "SUMMARY:Second renamed" in second
    assert api.since_calls == [(1, None), (1, 100)]
    assert json.loads(state.read_text(encoding="utf-8"))["calendars"]["1"]["since"] == 200


def test_main_public_calendar_requires_code():
    assert entry.main(["--public-calendar"]) == entry.EXIT_USAGE_ERROR


def test_main_maps_authentication_errors_to_exit_code(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise InvalidCredentialsError("Wrong email or password")

    monkeypatch.setattr(entry, "authenticate", _raise)
    monkeypatch.setenv("TIMETREE_EMAIL", "a@example.com")
    monkeypatch.setenv("TIMETREE_PASSWORD", "x")

    assert entry.main([]) == entry.EXIT_AUTH_ERROR


def test_main_maps_request_errors_to_exit_code(monkeypatch):
    class _BrokenApi(_FakeApi):
        def get_metadata(self):
            raise HTTPError("Failed to get calendar metadata")

    monkeypatch.setattr(entry, "authenticate", lambda *_a, **_k: _BrokenApi([], {}, labels={}))

    assert entry.main(["--session-id", "s"]) == entry.EXIT_ERROR


def test_main_no_active_calendars_is_usage_error(monkeypatch):
    api = _FakeApi(
        calendars=[{**_calendar(3, "Old", "old"), "deactivated_at": 1}], events_by_calendar={}
    )
    monkeypatch.setattr(entry, "authenticate", lambda *_a, **_k: api)

    assert entry.main(["--session-id", "s"]) == entry.EXIT_USAGE_ERROR


def test_authenticate_requires_credentials_without_session():
    with pytest.raises(entry.UsageError):
        entry.authenticate(None, None, None)


def test_authenticate_uses_login_when_no_session(monkeypatch):
    monkeypatch.setattr(entry, "login", lambda email, password: f"sid-{email}-{password}")
    created = {}

    class _Recorder:
        def __init__(self, session_id):
            created["session_id"] = session_id

    monkeypatch.setattr(entry, "TimeTreeCalendar", _Recorder)

    entry.authenticate("me@example.com", "pw", None)
    assert created["session_id"] == "sid-me@example.com-pw"

    entry.authenticate(None, None, "existing")
    assert created["session_id"] == "existing"


def test_list_labels_for_multiple_calendars(fake_api, capsys):
    fake_api.labels = {1: {"name": "Home", "color": "#ff0000"}}

    code = entry.main(["--session-id", "s", "-c", "fam,work", "--list-labels"])

    out = capsys.readouterr().out
    assert code == entry.EXIT_OK
    assert "Labels of calendar 'Family'" in out
    assert "Labels of calendar 'Work'" in out
    assert out.count("Home") == 2


def test_main_invalid_date_is_usage_error(fake_api):
    assert entry.main(["--session-id", "s", "-c", "fam", "--from", "01.02.2024"]) == (
        entry.EXIT_USAGE_ERROR
    )
