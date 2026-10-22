"""Tests for CLI argument helpers."""

import pytest

from timetree_exporter.calendar import Calendar
from timetree_exporter.cli import (
    SESSION_ID_ENV,
    output_path_for_calendar,
    parse_args,
    parse_calendar_codes,
    resolve_email,
    resolve_password,
    resolve_session_id,
)


def test_parse_args_defaults():
    args = parse_args([])

    assert args.output.endswith("timetree.ics")
    assert args.session_id is None
    assert args.date_from is None
    assert args.date_to is None
    assert args.sync_state is None
    assert args.include_attendees is False
    assert args.num_workers == 10


def test_parse_args_new_options():
    args = parse_args(
        [
            "--session-id",
            "abc",
            "--from",
            "2024-01-01",
            "--to",
            "2024-12-31",
            "--sync-state",
            "state.json",
            "--include-attendees",
            "-c",
            "a,b",
        ]
    )

    assert args.session_id == "abc"
    assert args.date_from == "2024-01-01"
    assert args.date_to == "2024-12-31"
    assert args.sync_state == "state.json"
    assert args.include_attendees is True
    assert parse_calendar_codes(args.calendar_code) == ["a", "b"]


def test_parse_args_version_exits(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--version"])

    assert exc_info.value.code == 0
    assert "timetree_exporter" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("abc", ["abc"]),
        (" a , b ,, c ", ["a", "b", "c"]),
    ],
)
def test_parse_calendar_codes(value, expected):
    assert parse_calendar_codes(value) == expected


def test_resolve_session_id_prefers_cli_then_env(monkeypatch):
    monkeypatch.delenv(SESSION_ID_ENV, raising=False)
    assert resolve_session_id(None) is None

    monkeypatch.setenv(SESSION_ID_ENV, "from-env")
    assert resolve_session_id(None) == "from-env"
    assert resolve_session_id("from-cli") == "from-cli"


def test_resolve_email_and_password_use_env_or_prompt(monkeypatch):
    monkeypatch.setenv("TIMETREE_EMAIL", "env@example.com")
    monkeypatch.setenv("TIMETREE_PASSWORD", "env-pass")
    assert resolve_email(None) == "env@example.com"
    assert resolve_email("cli@example.com") == "cli@example.com"
    assert resolve_password() == "env-pass"

    monkeypatch.delenv("TIMETREE_EMAIL")
    monkeypatch.delenv("TIMETREE_PASSWORD")
    monkeypatch.setattr("builtins.input", lambda _prompt: "typed@example.com")
    monkeypatch.setattr("timetree_exporter.cli.safe_getpass", lambda **_kwargs: "typed-pass")
    assert resolve_email(None) == "typed@example.com"
    assert resolve_password() == "typed-pass"


def test_output_path_for_calendar_single_calendar_is_unchanged():
    calendar = Calendar(None, {"id": 1, "name": "Family", "alias_code": "fam"})

    assert str(output_path_for_calendar("out/timetree.ics", calendar, multiple=False)) == str(
        output_path_for_calendar("out/timetree.ics", calendar, False)
    )
    assert output_path_for_calendar("out/timetree.ics", calendar, False).name == "timetree.ics"


def test_output_path_for_calendar_adds_calendar_suffix_when_multiple():
    calendar = Calendar(None, {"id": 1, "name": "Work / Home", "alias_code": "wh"})
    unnamed = Calendar(None, {"id": 2, "name": "", "alias_code": "code-2"})

    assert (
        output_path_for_calendar("timetree.ics", calendar, True).name == "timetree_Work___Home.ics"
    )
    assert output_path_for_calendar("timetree", calendar, True).name == "timetree_Work___Home.ics"
    assert output_path_for_calendar("timetree.ics", unnamed, True).name == "timetree_code-2.ics"
