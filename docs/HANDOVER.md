# Handover notes

Everything a new maintainer (human or AI) needs to pick this fork up: where it
came from, what was changed and why, what was **not** verified, and sensible
next steps. Keep this file current when you make non-obvious decisions.

## Repository relationships

- **Upstream:** `eoleedi/TimeTree-Exporter` (author 蔡鳳駿 / Fong-Chun Tsai),
  published on PyPI as `timetree-exporter` and on Homebrew as
  `eoleedi/tap/timetree-exporter`.
- **This fork:** `machaj25/TimeTree-Exporter`, owned by Jakub Machacek. It is
  the only git remote. Nothing here has been proposed upstream.
- The fork diverged from upstream commit `fd2f1a2` (2026-10-20, upstream
  version 0.9.0). Version in `pyproject.toml` is still `0.9.0`; release-please
  would bump it on the next release, but releasing from the fork is not set
  up (see "CI caveats").

### Syncing with upstream later

```bash
git remote add upstream https://github.com/eoleedi/TimeTree-Exporter.git
git fetch upstream
git merge upstream/main
```

Expect conflicts in `timetree_exporter/api/calendar.py`, `cli.py`,
`__main__.py`, `exporter.py` and `tests/` because this fork rewrote large
parts of them. Two policy conflicts will recur on every sync:

1. Upstream keeps `README.zh-tw.md`; this fork is English-only and deleted it
   together with the language switcher at the top of `README.md`. Delete it
   again after merging.
2. Upstream test fixtures use Chinese strings; this fork translated them.
   Re-translate anything new.

## Timeline of changes in this fork

| Date | Commit | What |
| --- | --- | --- |
| 2026-10-22 | `728787f` (PR #1) | `uv lock --upgrade`; fixed advisories in urllib3 2.7.0 and certifi 2026.5.20. |
| 2026-10-22 | `f0e01a2` (PR #2) | Hardening and features, see below. |
| 2026-10-22 | `a89133f` (PR #2) | English-only repository. |
| 2026-11-03 | this change | Documentation for handover (`CLAUDE.md`, `docs/`). |

### Security review that started the work

A review of upstream 0.9.0 found no malicious code and verified the PyPI sdist
was byte-identical to the repository. Findings that were acted on:

- No request timeouts (only image downloads had one).
- No retry or backoff despite documented rate limiting.
- Login failures logged the full response body at ERROR level, which can
  include the account email.
- `main()` let every exception escape as a traceback; `get_events` continued
  after a non-200 response and crashed on a missing key.
- Enums were declared as `@dataclass class X(enumerate)`, which only worked by
  accident.
- Entry point and CLI had 0 % and 50 % test coverage.

### Decisions and rationale

- **Retry only GET.** Login is a `PUT`; retrying it on 429 would worsen rate
  limiting and could lock the account. `urllib3.Retry.allowed_methods` is
  therefore `{GET, HEAD}`.
- **No server-side sign-out.** No sign-out endpoint could be verified (the
  environment used for this work could not reach `timetreeapp.com`). Rather
  than guess one, `close()` clears the cookie locally and the README says
  the web session stays valid until it expires.
- **`--session-id` instead of storing passwords.** Lets automation and
  future GUIs avoid handling the password at all.
- **Deactivated events are dropped everywhere**, not only in incremental
  mode. Upstream's roadmap describes `deactivated_at` as a sync tombstone, so
  exporting such events would export deleted ones. If a live run shows
  `deactivated_at` means something else, revisit `split_deactivated_events`.
- **Sync state stores full event JSON**, not just cursors, so a complete
  `.ics` can be regenerated without refetching. The file can get large for
  big calendars; that is accepted. Format is versioned (`"version": 1`) and
  unknown versions raise.
- **Date filter is conservative for recurring events.** A series starting
  before `--from` is kept if it starts on or before `--to`. Expanding RRULEs
  to decide precisely was judged not worth the complexity.
- **Attendees are opt-in** and use `urn:x-timetree:user:<id>` because TimeTree
  exposes no e-mail addresses and some clients ignore or reject attendees
  without `mailto:`.
- **Multi-calendar output naming** appends the sanitised calendar name to
  the output stem (`out_Family.ics`), falling back to `alias_code`/id for
  unnamed calendars.
- **mypy is permissive on purpose** (`ignore_missing_imports`, no
  `disallow_untyped_defs`) so it could be introduced without annotating every
  upstream function. Tightening it is a reasonable follow-up.
- **The example GitHub Actions workflow lives in `examples/`, not
  `.github/workflows/`**, so it does not run in this repository and leak
  nothing. Users copy it into their own repo.

## What was verified, and how

| Check | Result |
| --- | --- |
| `uv run ruff check`, `uv run ruff format --check` | clean |
| `uv run mypy` | clean, 16 files |
| `uv run pytest` | 141 passed; coverage 91 % |
| `pip-audit` on the runtime lock | no known vulnerabilities |
| `uv build --wheel` + install into a clean venv | `timetree-exporter --help` works, usage errors exit 3 |
| CI workflow YAML, pre-commit YAML, example workflow YAML | parse |

## What was NOT verified

These are the first things to check with real credentials.

1. **Any request against the live TimeTree API.** The development
   environment's proxy blocked `timetreeapp.com`. In particular:
   - that `events/sync` returns `since` on the final (`chunk: false`) page
     (if not, `--sync-state` never gets a cursor and always does a full
     fetch, which is safe but pointless);
   - that `since` can be passed back as-is (`?since=<value>`);
   - that deleted events really arrive with `deactivated_at` on incremental
     fetches;
   - that `attendees` on private events is a list of user ids.
2. **Docker image build.** No Docker daemon was available; the wheel build
   and install steps that the Dockerfile performs were verified separately.
3. **Windows.** CI runs the suite on Windows, but the author did not. The
   sync state uses `Path.replace`, which is fine on Windows; image paths are
   escaped for Windows-reserved names.
4. **GitHub Actions in the fork.** `release-please.yml` needs
   `MY_RELEASE_PLEASE_TOKEN` and will fail on every push to `main` in the
   fork; `python-publish.yml` and `bump-homebrew-formula.yml` need the PyPI
   trusted-publisher setup and `HOMEBREW_FORMULA_TOKEN`. Only
   `lint-and-test.yml` is expected to be green. Disable the others in the
   fork's Actions settings or add the secrets if you want releases.

## Known limitations and risks

- The whole tool depends on an unofficial API and the `X-Timetreea:
  web/2.1.0/en` header. Any change on TimeTree's side can break it; the
  README warns users.
- Accounts that sign in through Apple or Google cannot use email/password;
  `--session-id` is the workaround.
- Rate limits are real. `--include-comments`/`--include-images` with the
  default 10 workers can trip them on large calendars; `--num-workers 2` is
  safer.
- `--developer-mode` writes the complete calendar content (including other
  members' data) to disk in clear text.
- ICS import into Google Calendar is one-shot; deletions in TimeTree do not
  propagate to an already imported calendar. Subscribing to a published `.ics`
  URL is the way to get updates (see "Ideas").

## Ideas for next steps

Ordered by value for the owner's stated goal of using the data on an Android
phone.

1. **Publish-and-subscribe pipeline.** Run the exporter on a schedule
   (the example workflow or a small server), upload the `.ics` to an
   unguessable URL, subscribe to it from Google Calendar. No Android code
   needed; updates and deletions propagate automatically. Needs a hosting
   choice (object storage with a long random key, a Worker, a private
   WebDAV) and a tiny upload step added to the workflow.
2. **Android app with embedded Python (Chaquopy).** Layers 1 and 2 of the
   code are pure Python with pure-Python dependencies, so they can be bundled
   unchanged. A Kotlin UI would handle sign-in (store the session id in
   Android Keystore), calendar choice, and write events into
   `CalendarContract` instead of producing a file. `pwinput` is CLI-only and
   must not be imported on Android.
3. **Termux** already runs the unmodified package (`pip install
   timetree-exporter`), useful for a quick personal setup.
4. **Tighten mypy** (`disallow_untyped_defs`) and annotate the remaining
   upstream functions.
5. **Expand RRULEs in the date filter** with `dateutil.rrule` if precise
   `--from` filtering of recurring series is ever needed.
6. **Verify or discover a sign-out endpoint** by inspecting the TimeTree web
   client, then call it from `TimeTreeCalendar.close()`.
7. **Propose selected changes upstream** (timeouts, retry, login logging,
   exit codes) as small PRs; they are generic improvements the upstream author
   may welcome. Keep fork-specific policy (English-only) out of them.

## How to work in this repository

- Branch from `main`, commit with Conventional Commits, push to the fork, open
  a PR against the fork's `main`, let `lint-and-test.yml` pass, merge.
- Before pushing: `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`.
- Do not hand-edit `CHANGELOG.md`, `.release-please-manifest.json` or the
  version field; release-please owns them.
- When adding a CLI option: add it in `cli.py`, thread it through
  `__main__.run()` and `Exporter`, document it in `README.md` and in the
  options table of `docs/ARCHITECTURE.md`, and add an end-to-end test in
  `tests/test_entrypoint.py`.
- When adding an endpoint: put the URL in `api/calendar.py`, use `_get()`,
  record raw responses with `_record_raw_response()` so `--developer-mode`
  captures them, and add it to the endpoint table in
  `docs/ARCHITECTURE.md`.
