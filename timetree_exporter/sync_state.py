"""Persistent sync state for incremental exports.

TimeTree's events sync endpoint accepts a ``since`` cursor and returns only
events changed after it, including tombstones for deleted events. The state
file keeps the last cursor and the full set of known events per calendar so a
later run can fetch just the changes and still regenerate a complete ICS file.
"""

import json
import logging
from pathlib import Path
from typing import Any

from timetree_exporter.api.calendar import EventPage

logger = logging.getLogger(__name__)

STATE_VERSION = 1


class SyncState:
    """JSON-backed store of per-calendar sync cursors and known events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {"version": STATE_VERSION, "calendars": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Sync state file {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise ValueError(
                f"Sync state file {self.path} has an unsupported format; delete it to start over"
            )
        calendars = data.get("calendars")
        self._data["calendars"] = calendars if isinstance(calendars, dict) else {}

    def _calendar(self, calendar_id: Any) -> dict[str, Any]:
        return self._data["calendars"].setdefault(str(calendar_id), {"since": None, "events": {}})

    def since(self, calendar_id: Any) -> int | None:
        """Return the stored sync cursor for a calendar, or None for a first run."""
        return self._calendar(calendar_id).get("since")

    def known_events(self, calendar_id: Any) -> list[dict[str, Any]]:
        """Return the events currently stored for a calendar."""
        return list(self._calendar(calendar_id)["events"].values())

    def apply(self, calendar_id: Any, page: EventPage, full: bool) -> list[dict[str, Any]]:
        """Merge a fetched page into the state and return the resulting event list.

        ``full`` marks a fetch without a cursor; the stored events are then
        replaced instead of merged, which also purges anything the server no
        longer reports.
        """
        entry = self._calendar(calendar_id)
        events: dict[str, dict[str, Any]] = {} if full else dict(entry["events"])

        for deleted_uuid in page.deleted_uuids:
            events.pop(deleted_uuid, None)
        for event in page.events:
            event_uuid = event.get("uuid")
            if event_uuid:
                events[str(event_uuid)] = event

        entry["events"] = events
        if page.since is not None:
            entry["since"] = page.since
        logger.info(
            "Sync state for calendar %s: %d changed, %d deleted, %d total events",
            calendar_id,
            len(page.events),
            len(page.deleted_uuids),
            len(events),
        )
        return list(events.values())

    def save(self) -> None:
        """Write the state file atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
        logger.info("Sync state saved to %s", self.path.resolve())
