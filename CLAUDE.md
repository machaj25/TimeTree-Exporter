# CLAUDE.md

Guidance for AI coding assistants (and humans) working in this repository.
Read this first, then `docs/HANDOVER.md` for history and open items and
`docs/ARCHITECTURE.md` for how the code fits together.

## What this project is

`timetree-exporter` is a Python CLI that signs in to TimeTree with the
unofficial web API, downloads a calendar, and writes it as an RFC 5545 `.ics`
file. This repository is a fork of `eoleedi/TimeTree-Exporter`; see
`docs/HANDOVER.md` for how the fork differs and how to sync with upstream.

## Ground rules

- **Language.** Code, comments, docs, commit messages and PR text are English.
  The repository is intentionally English-only (no translated READMEs). The
  copyright holder's name in `LICENSE` is the one exception and must not be
  changed.
- **Do not push to upstream.** The only remote is the fork
  `machaj25/TimeTree-Exporter`. Never open PRs against `eoleedi/...` unless the
  owner explicitly asks.
- **Commit style.** Conventional Commits (`feat:`, `fix:`, `docs:`, `build:`,
  `chore:`); release-please reads them to build `CHANGELOG.md` and bump the
  version. Do not edit `CHANGELOG.md` or the version in `pyproject.toml` by
  hand.
- **Never log credentials or response bodies at INFO/ERROR level.** Login
  response bodies can echo the account email; keep them at DEBUG.
- **Never guess TimeTree endpoints.** The API is reverse-engineered. Only
  endpoints already in `timetree_exporter/api/` are known to work. If you need
  a new one, say so rather than inventing it (e.g. there is no verified
  sign-out endpoint).
- **No network in tests.** Tests replace `TimeTreeCalendar.session` with fake
  session objects (see `tests/test_calendar.py`) or monkeypatch
  `requests.put` (see `tests/test_auth.py`).

## Commands

```bash
uv sync --dev                 # install everything (uv is the only supported tool)
uv run ruff check             # lint
uv run ruff format --check    # formatting (CI enforces it)
uv run mypy                   # type-check timetree_exporter/
uv run pytest                 # tests (141 at time of writing, < 1 s)
uv run pytest --cov=timetree_exporter --cov-report=term-missing
uv run pre-commit run --all-files
uv run timetree-exporter --help
```

All four checks (ruff check, ruff format --check, mypy, pytest) must pass
before a push; CI runs them on Linux and Windows for Python 3.10 to 3.14.

## Where things live

| Path | Purpose |
| --- | --- |
| `timetree_exporter/__main__.py` | CLI entry point: auth, calendar selection, error-to-exit-code mapping |
| `timetree_exporter/cli.py` | argparse options, env var resolution, output naming |
| `timetree_exporter/api/` | HTTP layer: `const.py`, `session.py` (retry), `auth.py` (login), `calendar.py` (endpoints) |
| `timetree_exporter/calendar.py` | `Calendar` domain object wrapping one selected calendar |
| `timetree_exporter/exporter.py` | Orchestrates fetch, filter, format and write |
| `timetree_exporter/event.py` | `TimeTreeEvent` / `TimeTreePublicEvent` dataclasses parsed from API JSON |
| `timetree_exporter/formatter.py` | `ICalEventFormatter`: one TimeTree event to one `VEVENT` |
| `timetree_exporter/filters.py` | `--from` / `--to` date range filter |
| `timetree_exporter/sync_state.py` | `--sync-state` incremental export store |
| `timetree_exporter/utils.py` | timestamps, VTIMEZONE bounding, password prompt |
| `tests/` | pytest suite; fixtures in `tests/conftest.py` |
| `examples/scheduled-export.yml` | GitHub Actions template for nightly exports |
| `Dockerfile` | Container image (not built in CI) |
| `docs/` | Architecture and handover notes |

## Conventions worth knowing

- Timestamps from TimeTree are **milliseconds** since the epoch; divide by
  1000 before `datetime.fromtimestamp`. Use `utils.convert_timestamp_to_datetime`
  so negative timestamps work on Windows.
- All HTTP calls go through `TimeTreeCalendar._get`, which adds headers and a
  timeout. Do not call `self.session.get` directly except for streaming
  downloads.
- Public calendars (`--public-calendar`) use API v2, need no login, and go
  through `TimeTreePublicEvent`; private calendars use API v1 and
  `TimeTreeEvent`. Check `Calendar.is_public` before assuming either.
- Deactivated events (`deactivated_at` set) are deletions. They are dropped
  from exports and removed from the sync state.
- Optional features that cost extra requests per event (`--include-comments`,
  `--include-images`) are opt-in and must stay opt-in.
- `--include-attendees` is opt-in because TimeTree has no e-mail addresses;
  addresses are `urn:x-timetree:user:<id>`.
- Line length is 100 (ruff). Type hints are expected on new code; mypy runs
  with `check_untyped_defs`.
