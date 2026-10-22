"""Tests for the incremental sync state store."""

import json

import pytest

from timetree_exporter.api.calendar import EventPage
from timetree_exporter.sync_state import SyncState


def test_first_run_stores_full_fetch_and_cursor(tmp_path):
    state = SyncState(tmp_path / "state.json")
    assert state.since(1) is None

    events = state.apply(
        1,
        EventPage(events=[{"uuid": "a", "title": "A"}, {"uuid": "b", "title": "B"}], since=100),
        full=True,
    )
    state.save()

    assert sorted(e["uuid"] for e in events) == ["a", "b"]
    reloaded = SyncState(tmp_path / "state.json")
    assert reloaded.since(1) == 100
    assert sorted(e["uuid"] for e in reloaded.known_events(1)) == ["a", "b"]


def test_incremental_run_merges_updates_and_tombstones(tmp_path):
    state = SyncState(tmp_path / "state.json")
    state.apply(
        1,
        EventPage(events=[{"uuid": "a", "title": "A"}, {"uuid": "b", "title": "B"}], since=100),
        full=True,
    )

    events = state.apply(
        1,
        EventPage(
            events=[{"uuid": "b", "title": "B updated"}, {"uuid": "c", "title": "C"}],
            deleted_uuids=["a"],
            since=200,
        ),
        full=False,
    )

    by_uuid = {e["uuid"]: e for e in events}
    assert set(by_uuid) == {"b", "c"}
    assert by_uuid["b"]["title"] == "B updated"
    assert state.since(1) == 200


def test_state_is_kept_per_calendar(tmp_path):
    state = SyncState(tmp_path / "state.json")
    state.apply(1, EventPage(events=[{"uuid": "a"}], since=10), full=True)
    state.apply(2, EventPage(events=[{"uuid": "z"}], since=20), full=True)

    assert state.since(1) == 10
    assert state.since(2) == 20
    assert [e["uuid"] for e in state.known_events(2)] == ["z"]


def test_missing_cursor_keeps_previous_cursor(tmp_path):
    state = SyncState(tmp_path / "state.json")
    state.apply(1, EventPage(events=[{"uuid": "a"}], since=10), full=True)
    state.apply(1, EventPage(events=[{"uuid": "b"}], since=None), full=False)

    assert state.since(1) == 10


def test_save_writes_atomically_and_creates_parent(tmp_path):
    path = tmp_path / "nested" / "state.json"
    state = SyncState(path)
    state.apply(1, EventPage(events=[{"uuid": "a"}], since=1), full=True)

    state.save()

    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_invalid_state_file_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        SyncState(path)

    path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported format"):
        SyncState(path)
