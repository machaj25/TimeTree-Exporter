# Architecture

How `timetree-exporter` is put together, what talks to what, and the data
formats involved. For setup and commands see `../DEVELOPMENT.md`; for history
and open items see `HANDOVER.md`.

## Big picture

```
CLI args / env ──► __main__.run()
                     │
                     ├─ authenticate()  ──► api.auth.login()  (PUT /auth/email/signin)
                     │                       or reuse --session-id
                     │
                     ├─ select_calendars() ──► api.calendar.get_metadata()  (GET /calendars)
                     │                          └─ Calendar(api, metadata) per selection
                     │
                     └─ for each Calendar: Exporter(...).export()
                           │
                           ├─ _fetch_events()
                           │     ├─ full:        Calendar.get_events()        → list[dict]
                           │     └─ incremental: Calendar.get_events_since() → EventPage
                           │                     SyncState.apply() + save()
                           ├─ filters.filter_events_by_date()
                           ├─ download_event_images()           (optional)
                           ├─ Calendar.get_labels()
                           ├─ build_single_calendar() / group_events_by_label()
                           │     └─ event_cls.from_dict() → ICalEventFormatter.to_ical()
                           └─ write_calendar()  → utils.add_bounded_timezones_before_events()
                                                  → .ics on disk
```

Three layers, each importable without the one above it:

1. **HTTP layer** (`timetree_exporter/api/`): knows URLs, headers, cookies,
   retries, timeouts, pagination. Returns plain `dict`/`list` JSON and the
   `EventPage` dataclass. No knowledge of iCalendar.
2. **Domain layer** (`calendar.py`, `event.py`, `formatter.py`, `filters.py`,
   `sync_state.py`, `exporter.py`): turns JSON into dataclasses, filters,
   merges incremental state, and renders `VEVENT`s. Talks to the HTTP layer
   only through the `CalendarApi` protocol in `calendar.py`, which is why tests
   can pass in-memory fakes.
3. **CLI layer** (`cli.py`, `__main__.py`): argument parsing, prompts,
   credential resolution, output naming, exit codes.

A GUI or mobile wrapper would reuse layers 1 and 2 unchanged and replace
layer 3.

## HTTP layer

### Endpoints in use

| Purpose | Method and path | Notes |
| --- | --- | --- |
| Sign in | `PUT /api/v1/auth/email/signin` | Body `{uid, password, uuid}`; session comes back as the `_session_id` cookie. Error codes: `-702` wrong credentials, `-495` rate limited. |
| List calendars | `GET /api/v1/calendars?since=0` | Returns `calendars[]` with `id`, `name`, `alias_code`, `deactivated_at`, `calendar_users[]`. |
| Labels | `GET /api/v1/calendar/{id}/labels` | `calendar_labels[]` with `id`, `name`, integer `color`. A non-200 only logs a warning. |
| Events (sync) | `GET /api/v1/calendar/{id}/events/sync[?since=N]` | Returns `events[]`, `chunk` (bool) and `since` (next cursor). Follow while `chunk` is true. Events with `deactivated_at` set are tombstones. |
| Event activities | `GET /api/v1/calendar/{id}/event/{uuid}/activities?since=N` | Comments and image attachments; paginated the same way. Only used with `--include-comments` / `--include-images`. |
| Image download | `GET https://attachments.timetreeapp.com/{object_key}` | Streamed to disk. |
| Public calendar metadata | `GET /api/v2/public_calendars/{id}` | Labels for public calendars; no login. |
| Public events | `GET /api/v2/public_calendars/{id}/public_events?from=0[&cursor=...]` | Cursor pagination via `paging.next` / `paging.next_cursor`. |

Every request sends `Content-Type: application/json` and
`X-Timetreea: web/2.1.0/en` (the web client identifier). Constants live in
`api/const.py`.

There is **no known sign-out endpoint**. `TimeTreeCalendar.close()` only
clears the cookie locally and closes the connection pool.

### Session, retries, timeouts

`api/session.py` builds one `requests.Session` per `TimeTreeCalendar`:

- `HTTPAdapter` with `urllib3.Retry(total=5, backoff_factor=1.0,
  status_forcelist=(429, 500, 502, 503, 504), allowed_methods={GET, HEAD},
  respect_retry_after_header=True, raise_on_status=False)`.
  Backoff is exponential (1 s, 2 s, 4 s, ...) unless the server sends
  `Retry-After`. Only idempotent methods retry, so the login `PUT` never does.
- `TimeTreeCalendar._get()` adds the standard headers and
  `REQUEST_TIMEOUT = (10, 30)` seconds (connect, read). Image downloads use
  `ATTACHMENT_TIMEOUT = (10, 60)`.

### Pagination and `EventPage`

`_fetch_event_pages(calendar_id, since)` loops over the sync endpoint and
returns:

```python
@dataclass
class EventPage:
    events: list[dict]        # active events, deactivated ones removed
    deleted_uuids: list[str]  # uuids of events with deactivated_at set
    since: int | None         # last cursor returned by the server
```

`get_events()` is the full-fetch convenience that returns `page.events`;
`get_events_since(since)` returns the whole `EventPage` for incremental use.
When `--include-comments` / `--include-images` is set, `add_event_activities`
fans out one activities request per event over a `ThreadPoolExecutor`
(`--num-workers`, default 10).

## Domain layer

### `Calendar` (`calendar.py`)

Frozen dataclass of `(api, metadata)`. Properties: `id`, `name`,
`alias_code`, `is_public`, `calendar_users`, `user_names()`,
`supports_incremental_sync`. Methods delegate to the API and pick the public
or private endpoint. The `CalendarApi` protocol documents exactly which API
methods the domain layer needs; tests implement it with small fake classes.

### Events (`event.py`)

`TimeTreeEvent.from_dict()` maps private event JSON to a dataclass. All
fields are `Optional` because payloads do omit them. Notable fields:

- `label_id`: read from `label_id` or the JSON:API
  `relationships.label.data.id` form (`"calendar_id,label_number"`).
- `attendees`: list of user ids (dicts with `user_id`/`id` are tolerated).
- `deactivated_at`: tombstone marker.
- `event_type` (`TimeTreeEventType`: NORMAL=0, BIRTHDAY=1) and `category`
  (`TimeTreeEventCategory`: NORMAL=1, MEMO=2). Birthdays and memos are skipped
  by the formatter.

`TimeTreePublicEvent` extends it for API v2 payloads (`id` instead of `uuid`,
`link_url`, cover images, hashtags as categories, label name/color embedded in
the event).

### Formatter (`formatter.py`)

`ICalEventFormatter(event, label_name, color, category_names, attendees)`
exposes one property per iCalendar field and `to_ical()` assembles the
`VEVENT`. Mapping rules:

- Timestamps are milliseconds. `created_at`/`updated_at` → `CREATED` /
  `LAST-MODIFIED` (omitted when missing). `start_at`/`end_at` → `DTSTART` /
  `DTEND`; events missing either are skipped with a warning.
- All-day events become `VALUE=DATE` with an **exclusive** `DTEND`
  (`end + 1 day`), per RFC 5545.
- Timed events carry `TZID=<start_timezone>` unless the zone is UTC. Missing
  timezone falls back to UTC.
- `recurrences` are raw iCalendar lines (`RRULE:...`, `EXDATE:...`,
  `RDATE:...`) parsed with `icalendar.Contentline`. A date-only `UNTIL` on a
  timed event is converted to 23:59:59 local, then UTC.
- `alerts` (minutes before) → `VALARM` with `ACTION:DISPLAY`.
- `recurring_uuid` or `parent_id` → `RELATED-TO`.
- Attendees → `ATTENDEE;CN=<name>;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT:urn:x-timetree:user:<id>`.
- Label → `CATEGORIES` and `COLOR` (hex, validated).

`utils.add_bounded_timezones_before_events()` adds `VTIMEZONE` components
limited to the date range actually used, because Google Calendar chokes on
very long timezone definitions.

### Date filter (`filters.py`)

`--from` / `--to` are inclusive dates compared against the event's start and
end dates computed **in the event's own timezone**. Events without a start
are kept. Recurring events are kept whenever they start on or before `--to`
because later occurrences may fall inside the range; this is deliberately
conservative (it can include a recurring series whose occurrences all end
before `--from`).

### Sync state (`sync_state.py`)

`--sync-state FILE` stores, per calendar:

```json
{
  "version": 1,
  "calendars": {
    "<calendar id>": {
      "since": 1713120000000,
      "events": { "<event uuid>": { ...raw event JSON incl. comments/_image_attachments... } }
    }
  }
}
```

Flow in `Exporter._fetch_events()`:

1. `since = state.since(calendar.id)`; `None` means first run.
2. `page = calendar.get_events_since(since)`.
3. `state.apply(calendar.id, page, full=since is None)`: on a full run the
   stored events are replaced; otherwise tombstones are removed and changed
   events upserted. The cursor is updated only when the server returned one.
4. `state.save()` writes atomically (temp file + `replace`).
5. The merged event list feeds the normal export, so the `.ics` is always
   complete.

Public calendars do not support this (`supports_incremental_sync` is false);
the CLI warns and does a full export. Unsupported `version` values raise so a
future format change cannot silently misread old files.

### Exporter (`exporter.py`)

`Exporter(calendar, output, split_by_label, include_comments,
num_workers, include_images, include_attendees, date_from, date_to,
sync_state).export()` is the single orchestration entry point. Helpers:

- `build_single_calendar()` and `group_events_by_label()` both call
  `format_event()`, which resolves attendee names from
  `Calendar.user_names()` when attendee export is on.
- `write_split_calendars()` names files `<stem>_<label>.ics`;
  `label_suffix_for_group()` sanitises label names.
- `download_event_images()` writes `timetree_images/<event uuid>/<object key>`
  next to the ICS plus `timetree_images.json` manifest; `_image_path()`
  rejects path traversal and escapes Windows-invalid characters.

## CLI layer

### Options (`cli.py`)

| Option | Env var | Effect |
| --- | --- | --- |
| `-o/--output PATH` | | Output `.ics` (default `./timetree.ics`). With several calendars, each file gets `_<calendar name>` appended. |
| `-e/--email` | `TIMETREE_EMAIL` | Sign-in email; prompted if absent. |
| | `TIMETREE_PASSWORD` | Password; masked prompt if absent. |
| `--session-id` | `TIMETREE_SESSION_ID` | Reuse a `_session_id` cookie; skips sign-in. |
| `-c/--calendar_code CODES` | | Comma-separated `alias_code`s. Unknown codes log an error and fall back to the prompt. With `--public-calendar`, the public calendar id. |
| `--public-calendar` | | Use API v2 without login. |
| `--list-labels` | | Print labels and exit (per calendar). |
| `--split-by-label` | | One `.ics` per label. |
| `--from/--to YYYY-MM-DD` | | Inclusive date range. |
| `--sync-state FILE` | | Incremental export store (private calendars). |
| `--include-comments` | | Extra request per event; comments → `COMMENT`. |
| `--include-images` | | Extra request per event; images saved beside the ICS. |
| `--include-attendees` | | `ATTENDEE` properties. |
| `--num-workers N` | | Threads for per-event requests (default 10, min 1). |
| `--developer-mode` | `TIMETREE_EXPORTER_DEVELOPER=1` | Dump raw API JSON to `raw-timetree/`. |
| `--raw-output-dir DIR` | | Custom dump directory. |
| `-v/--verbose` | | DEBUG logging (includes response bodies). |

Interactive calendar selection accepts `1`, `1,3` or `all`.

### Exit codes (`__main__.py`)

| Code | Meaning | Raised by |
| --- | --- | --- |
| 0 | success | |
| 1 | export or network failure | `requests.RequestException`, `ValueError`, `KeyError`, `OSError` |
| 2 | authentication failure | `AuthenticationError` and subclasses |
| 3 | invalid input | `UsageError` (bad dates, bad selection, missing code, no calendars) |
| 130 | interrupted | `KeyboardInterrupt` |

`main(argv)` returns the code; the console script wraps it in `sys.exit`.

## Testing strategy

- No test touches the network. The HTTP layer is tested by assigning a fake
  object with a `get(url, **kwargs)` method to `TimeTreeCalendar.session`
  (`tests/test_calendar.py`), and `requests.put` is monkeypatched for login
  (`tests/test_auth.py`).
- The domain layer is tested with fake `CalendarApi` implementations
  (`tests/test_main.py`, `tests/test_entrypoint.py`).
- `tests/test_entrypoint.py` drives `__main__.main(argv)` end to end with
  `authenticate` monkeypatched, covering multi-calendar output, date filter,
  sync state across two runs, attendees and every exit code.
- Fixtures in `tests/conftest.py` are representative private and public event
  payloads; one title contains a non-ASCII character on purpose to exercise
  UTF-8 output.

## Packaging and release

- Build backend is hatchling; `uv build` produces sdist and wheel. The sdist
  excludes tests, JSON and `uv.lock`.
- `release-please` (GitHub Action on push to `main`) opens a release PR from
  Conventional Commits and tags the release; the `python-publish.yml`
  workflow then publishes to PyPI via trusted publishing, and
  `bump-homebrew-formula.yml` updates the Homebrew tap. All three need
  repository secrets that exist upstream, not in this fork (see
  `HANDOVER.md`).
- `Dockerfile` builds a wheel in a builder stage and installs it into a
  slim runtime image running as a non-root user with `/export` as the volume.
