"""
This module logs in to TimeTree and converts TimeTree events to iCal format.
"""

import logging
import sys
from collections.abc import Sequence

from requests.exceptions import RequestException

from timetree_exporter.api.auth import AuthenticationError, login
from timetree_exporter.api.calendar import TimeTreeCalendar
from timetree_exporter.calendar import Calendar
from timetree_exporter.cli import (
    configure_logging,
    list_labels_and_exit,
    output_path_for_calendar,
    parse_args,
    parse_calendar_codes,
    resolve_email,
    resolve_password,
    resolve_session_id,
)
from timetree_exporter.config import configure_developer_mode
from timetree_exporter.exporter import Exporter
from timetree_exporter.filters import parse_iso_date
from timetree_exporter.sync_state import SyncState

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH_ERROR = 2
EXIT_USAGE_ERROR = 3
EXIT_INTERRUPTED = 130


class UsageError(ValueError):
    """Raised for invalid user input that is not caught by argparse."""


def authenticate(email: str | None, password: str | None, session_id: str | None):
    """Return an API client, signing in with email and password unless a session id is given."""
    if session_id:
        logger.info("Using the provided TimeTree session id, skipping sign-in")
        return TimeTreeCalendar(session_id)
    if not email or not password:
        raise UsageError("Email and password are required to sign in")
    return TimeTreeCalendar(login(email, password))


def _parse_selection(answer: str, count: int) -> list[int]:
    """Turn the interactive calendar answer into zero-based indexes."""
    answer = (answer or "1").strip().lower()
    if answer == "all":
        return list(range(count))
    indexes = []
    for part in answer.split(","):
        part = part.strip()
        if not part.isdigit() or not 1 <= int(part) <= count:
            raise UsageError(
                f"Invalid Calendar Number '{part}'. Must be a number between 1 and {count}, "
                "a comma-separated list of numbers, or 'all'"
            )
        index = int(part) - 1
        if index not in indexes:
            indexes.append(index)
    return indexes


def select_calendars(api: TimeTreeCalendar, calendar_codes: Sequence[str]) -> list[Calendar]:
    """Pick one or more calendars, by code when given, otherwise interactively."""
    metadatas = api.get_metadata()

    # Filter out deactivated calendars
    metadatas = [metadata for metadata in metadatas if metadata.get("deactivated_at") is None]

    if len(metadatas) == 0:
        raise UsageError("No active calendars found in this TimeTree account")

    if calendar_codes:
        selected = []
        missing = []
        for code in calendar_codes:
            matches = [metadata for metadata in metadatas if metadata.get("alias_code") == code]
            if matches:
                selected.append(matches[0])
            else:
                missing.append(code)
        if missing:
            logger.error("No calendars found with the specified code(s): %s", ", ".join(missing))
        if selected:
            for metadata in selected:
                print(f"Using calendar: {metadata['name']} (code: {metadata['alias_code']})")
            return [Calendar(api, metadata) for metadata in selected]
        logger.error("Falling back to interactive calendar selection")

    # Print out the list of calendars for the user to choose from
    for i, metadata in enumerate(metadatas):
        print(
            f"{i + 1}. {metadata['name'] if metadata['name'] else 'Unnamed'} "
            f"(code: {metadata['alias_code']})"
        )

    # Ask the user to choose one or more calendars
    answer = input("Which Calendar(s) do you want to export? (e.g. 1 or 1,3 or all; default 1): ")
    indexes = _parse_selection(answer, len(metadatas))
    return [Calendar(api, metadatas[index]) for index in indexes]


def select_calendar(email: str, password: str, calendar_code: str | None) -> Calendar:
    """Authenticate and select a single calendar (kept for backwards compatibility)."""
    api = authenticate(email, password, None)
    return select_calendars(api, parse_calendar_codes(calendar_code))[0]


def select_public_calendar(calendar_id: str | None) -> Calendar:
    """Select a public calendar by id."""
    if not calendar_id:
        raise UsageError(
            "--public-calendar requires -c/--calendar_code with the public calendar id"
        )
    calendar = TimeTreeCalendar(None)
    return Calendar(
        calendar,
        {
            "id": calendar_id,
            "name": f"Public Calendar {calendar_id}",
            "alias_code": calendar_id,
            "public": True,
        },
    )


def run(args) -> int:
    """Run the export for parsed arguments and return a process exit code."""
    configure_logging(args.verbose)
    configure_developer_mode(args.developer_mode, args.raw_output_dir)

    try:
        date_from = parse_iso_date(args.date_from)
        date_to = parse_iso_date(args.date_to)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    if date_from and date_to and date_from > date_to:
        raise UsageError("--from must not be later than --to")
    if args.num_workers < 1:
        raise UsageError("--num-workers must be at least 1")

    sync_state = SyncState(args.sync_state) if args.sync_state else None

    if args.public_calendar:
        calendars = [select_public_calendar(args.calendar_code)]
        if sync_state is not None:
            logger.warning("--sync-state is ignored for public calendars")
            sync_state = None
    else:
        session_id = resolve_session_id(args.session_id)
        if session_id:
            api = authenticate(None, None, session_id)
        else:
            email = resolve_email(args.email)
            password = resolve_password()
            api = authenticate(email, password, None)
        calendars = select_calendars(api, parse_calendar_codes(args.calendar_code))

    multiple = len(calendars) > 1
    try:
        for calendar in calendars:
            if args.list_labels:
                if multiple:
                    print(f"Labels of calendar '{calendar.name}':")
                list_labels_and_exit(calendar)
                continue

            output = output_path_for_calendar(args.output, calendar, multiple)
            Exporter(
                calendar,
                output,
                split_by_label=args.split_by_label,
                include_comments=args.include_comments,
                include_images=args.include_images,
                include_attendees=args.include_attendees,
                num_workers=args.num_workers,
                date_from=date_from,
                date_to=date_to,
                sync_state=sync_state,
            ).export()
    finally:
        for calendar in calendars:
            close = getattr(calendar.api, "close", None)
            if callable(close):
                close()
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Main function for the Timetree Exporter."""
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except AuthenticationError as exc:
        logger.error("Authentication failed: %s", exc)
        return EXIT_AUTH_ERROR
    except UsageError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR
    except RequestException as exc:
        logger.error("TimeTree request failed: %s", exc)
        logger.debug("Request failure details", exc_info=True)
        return EXIT_ERROR
    except (ValueError, KeyError, OSError) as exc:
        logger.error("Export failed: %s", exc)
        logger.debug("Export failure details", exc_info=True)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
