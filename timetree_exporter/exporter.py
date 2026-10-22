"""Build and write iCalendar exports from TimeTree events."""

import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from icalendar import Calendar as ICalendar

from timetree_exporter import ICalEventFormatter, TimeTreeEvent, TimeTreePublicEvent
from timetree_exporter.filters import filter_events_by_date
from timetree_exporter.sync_state import SyncState
from timetree_exporter.utils import add_bounded_timezones_before_events, escape_image_part

logger = logging.getLogger(__name__)


class Exporter:
    """Export a selected TimeTree calendar to one or more iCalendar files."""

    def __init__(
        self,
        calendar,
        output,
        split_by_label=False,
        include_comments=False,
        num_workers=10,
        include_images=False,
        include_attendees=False,
        date_from: date | None = None,
        date_to: date | None = None,
        sync_state: SyncState | None = None,
    ):
        self.calendar = calendar
        self.output = output
        self.split_by_label = split_by_label
        self.include_comments = include_comments
        self.include_images = include_images
        self.include_attendees = include_attendees
        self.num_workers = num_workers
        self.date_from = date_from
        self.date_to = date_to
        self.sync_state = sync_state

    def _fetch_events(self):
        """Fetch events, going through the sync state when incremental sync is enabled."""
        fetch_kwargs = {
            "include_comments": self.include_comments,
            "include_images": self.include_images,
            "num_workers": self.num_workers,
        }
        if self.sync_state is None:
            return self.calendar.get_events(**fetch_kwargs)

        if not self.calendar.supports_incremental_sync:
            logger.warning(
                "Incremental sync is not available for calendar '%s'; doing a full export",
                self.calendar.name,
            )
            return self.calendar.get_events(**fetch_kwargs)

        since = self.sync_state.since(self.calendar.id)
        if since is None:
            logger.info(
                "No sync cursor for calendar '%s' yet, fetching everything", self.calendar.name
            )
        else:
            logger.info("Fetching changes for calendar '%s' since %s", self.calendar.name, since)
        page = self.calendar.get_events_since(since, **fetch_kwargs)
        events = self.sync_state.apply(self.calendar.id, page, full=since is None)
        self.sync_state.save()
        return events

    def export(self):
        """Fetch labels and events, then write the configured iCalendar output."""
        events = self._fetch_events()
        logger.info("Found %d events", len(events))
        events = filter_events_by_date(events, self.date_from, self.date_to)

        if self.include_images and not self.calendar.is_public:
            download_event_images(self.calendar, events, self.output, self.num_workers)

        labels = self.calendar.get_labels()
        if self.calendar.is_public and not labels:
            labels = public_labels_from_events(events)
        logger.info("Found %d labels", len(labels))

        event_cls = TimeTreePublicEvent if self.calendar.is_public else TimeTreeEvent
        user_names = self.calendar.user_names() if self.include_attendees else None

        if self.split_by_label:
            grouped_events = group_events_by_label(
                events, labels, event_cls=event_cls, user_names=user_names
            )
            write_split_calendars(grouped_events, labels, self.output, len(events))
            return

        cal = build_single_calendar(events, labels, event_cls=event_cls, user_names=user_names)
        logger.info(
            "A total of %d/%d events are added to the calendar",
            len(cal.subcomponents),
            len(events),
        )
        write_calendar(cal, self.output)


def _package_version():
    try:
        return version("timetree_exporter")
    except PackageNotFoundError:  # pragma: no cover - only when run from an unbuilt checkout
        return "unknown"


def create_calendar():
    """Create a new iCalendar object with standard properties."""
    cal = ICalendar()
    cal.add("prodid", f"-//TimeTree Exporter {_package_version()}//EN")
    cal.add("version", "2.0")
    return cal


def sanitize_filename(name):
    """Sanitize a string for use as an export filename component."""
    return re.sub(r"[^\w\-]", "_", name).strip("_")


def write_calendar(cal, output_path: str | Path):
    """Write a calendar to an .ics file."""
    add_bounded_timezones_before_events(cal)

    path = Path(output_path)
    with path.open("wb") as f:
        f.write(cal.to_ical())
        logger.info("The .ics calendar file is saved to %s", path.resolve())


def _image_path(output_path, event_uuid, object_key):
    """Return a stable image path below the ICS output directory."""
    if (
        not isinstance(event_uuid, str)
        or not event_uuid
        or event_uuid in {".", ".."}
        or "/" in event_uuid
        or "\\" in event_uuid
    ):
        raise ValueError(f"Invalid event UUID: {event_uuid}")
    if not isinstance(object_key, str) or not object_key:
        raise ValueError(f"Invalid image object key: {object_key}")
    key_path = PurePosixPath(object_key)
    if key_path.is_absolute() or ".." in key_path.parts:
        raise ValueError(f"Invalid image object key: {object_key}")
    image_root = Path(output_path).parent / "timetree_images"
    image_path = (
        image_root / event_uuid / Path(*(escape_image_part(part) for part in key_path.parts))
    )
    try:
        image_path.resolve().relative_to(image_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Invalid image object key: {object_key}") from exc
    return image_path


def _event_start_date(event):
    """Return an event's local start date as an ISO string."""
    timestamp = event.get("start_at")
    if timestamp is None:
        return None
    timezone = ZoneInfo(event.get("start_timezone") or "UTC")
    return datetime.fromtimestamp(timestamp / 1000, timezone).date().isoformat()


def download_event_images(calendar, events, output, num_workers):
    """Download event images and write their event mapping manifest."""
    output_path = Path(output)
    manifest = []
    tasks = []

    for event in events:
        event_uuid = event.get("uuid")
        for image in event.get("_image_attachments", []):
            object_key = image["object_key"]
            try:
                image_path = _image_path(output_path, event_uuid, object_key)
            except ValueError:
                logger.warning("Skipping invalid image path for event %s", event_uuid)
                continue
            entry = {
                "event_uuid": event_uuid,
                "title": event.get("title"),
                "start_date": _event_start_date(event),
                "image_path": image_path.relative_to(output_path.parent).as_posix(),
                "object_key": object_key,
            }
            if not image_path.is_file():
                tasks.append((object_key, image_path, entry))
            else:
                manifest.append(entry)

    with ThreadPoolExecutor(max_workers=max(1, num_workers)) as executor:
        futures = {
            executor.submit(calendar.download_image, object_key, image_path): entry
            for object_key, image_path, entry in tasks
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                future.result()
                manifest.append(entry)
                logger.info("Downloaded image to %s", entry["image_path"])
            except Exception as exc:
                logger.warning("Failed to download image to %s: %s", entry["image_path"], exc)

    manifest.sort(key=lambda entry: (entry["event_uuid"], entry["object_key"]))

    manifest_path = output_path.parent / "timetree_images.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("The image manifest is saved to %s", manifest_path.resolve())


def public_labels_from_events(events):
    """Build label metadata from public calendar event payloads."""
    labels = {}
    for event in events:
        label = event.get("public_calendar_label") or {}
        label_id = label.get("label_id")
        if label_id is None:
            continue
        color = label.get("color")
        if color is None:
            color = event.get("color", "")
        labels[label_id] = {
            "name": str(label.get("name") or ""),
            "color": f"#{color:06x}" if isinstance(color, int) else color,
        }
    return labels


def resolve_attendees(time_tree_event, user_names):
    """Return attendee dicts with ids and display names for the formatter.

    ``user_names=None`` disables attendee export entirely.
    """
    if user_names is None:
        return None
    return [
        {"id": user_id, "name": user_names.get(user_id)}
        for user_id in getattr(time_tree_event, "attendees", None) or []
    ]


def format_event(time_tree_event, label_name, color, user_names=None):
    """Convert one TimeTree event into an iCalendar VEVENT (or None when skipped)."""
    formatter = ICalEventFormatter(
        time_tree_event,
        label_name=label_name,
        color=color,
        category_names=getattr(time_tree_event, "category_names", None),
        attendees=resolve_attendees(time_tree_event, user_names),
    )
    return formatter.to_ical()


def build_single_calendar(events, labels, event_cls=TimeTreeEvent, user_names=None):
    """Build single output calendar from events."""
    cal = create_calendar()
    label_lookup = {lid: info["name"] for lid, info in labels.items()} if labels else {}
    color_lookup = {lid: info["color"] for lid, info in labels.items()} if labels else {}

    for event in events:
        time_tree_event = event_cls.from_dict(event)
        label_name = label_lookup.get(time_tree_event.label_id) or getattr(
            time_tree_event, "label_name", None
        )
        color = color_lookup.get(time_tree_event.label_id) or getattr(
            time_tree_event, "label_color", None
        )
        ical_event = format_event(time_tree_event, label_name, color, user_names)
        if ical_event is not None:
            cal.add_component(ical_event)

    return cal


def group_events_by_label(events, labels, event_cls=TimeTreeEvent, user_names=None):
    """Group converted iCal events by label id (or None for unlabeled)."""
    grouped = defaultdict(list)

    for event in events:
        time_tree_event = event_cls.from_dict(event)
        label_info = labels.get(time_tree_event.label_id)
        if label_info is not None:
            label_name = label_info["name"]
            color = label_info["color"]
        else:
            label_name = getattr(time_tree_event, "label_name", None)
            color = getattr(time_tree_event, "label_color", None)
        ical_event = format_event(time_tree_event, label_name, color, user_names)

        if ical_event is None:
            continue

        group_key = time_tree_event.label_id if label_name or color else None
        grouped[group_key].append(ical_event)

    return grouped


def label_suffix_for_group(group_key, labels):
    """Return output filename suffix for a label group."""
    if group_key is None:
        return "unlabeled"
    name = sanitize_filename(labels[group_key]["name"])
    if name:
        return name
    return sanitize_filename(f"label_{group_key}")


def write_split_calendars(grouped_events, labels, output, event_count):
    """Write grouped events into separate calendar files."""
    output_path = Path(output)

    for group_key, ical_events in grouped_events.items():
        label_suffix = label_suffix_for_group(group_key, labels)
        cal = create_calendar()
        for ical_event in ical_events:
            cal.add_component(ical_event)
        if output_path.suffix:
            split_path = output_path.with_name(
                f"{output_path.stem}_{label_suffix}{output_path.suffix}"
            )
        else:
            split_path = output_path.with_name(f"{output_path.name}_{label_suffix}.ics")
        logger.info("%d events for label '%s'", len(ical_events), label_suffix)
        write_calendar(cal, split_path)

    total = sum(len(evts) for evts in grouped_events.values())
    logger.info(
        "A total of %d/%d events split into %d files",
        total,
        event_count,
        len(grouped_events),
    )
