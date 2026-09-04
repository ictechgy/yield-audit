"""Transcript adapter tests: schema handling and defensive parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from yield_audit import transcripts
from yield_audit.events import normalize_command, parse_iso8601


def test_load_fixture_sessions(fixture_env):
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"],
        fixture_env["transcripts_root"],
        now=parse_iso8601(fixture_env["now"]),
        days=30,
    )
    assert [s.session_id[:1] for s in sessions] == ["a", "b", "c"]

    session_a = sessions[0]
    assert len(session_a.api_calls) == 4
    assert session_a.ran_git_commit is True
    assert session_a.edited_files, "session A should have edited files"
    assert {f.rsplit("/", 1)[-1] for f in session_a.edited_files} == {
        "app.py",
        "notes.md",
        "config.yaml",
        "test_core.py",
    }

    session_b = sessions[1]
    assert session_b.edited_files == []
    assert session_b.ran_git_commit is False

    session_c = sessions[2]
    assert len(session_c.compact_boundaries) == 1


def test_tool_results_are_linked_by_tool_use_id(fixture_env):
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"],
        fixture_env["transcripts_root"],
        now=parse_iso8601(fixture_env["now"]),
        days=30,
    )
    session_b = sessions[1]
    sequence = session_b.bash_sequence()
    # (ts, normalized command, is_error) — whitespace in "npm  test" is normalized
    assert [(cmd, err) for _, cmd, err in sequence] == [
        ("npm test", True),
        ("npm test", True),
        ("pytest -q", False),
    ]


def test_time_window_filters_old_sessions(fixture_env):
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"],
        fixture_env["transcripts_root"],
        now=parse_iso8601(fixture_env["now"]),
        days=17,  # cutoff 08-03: session A (ended 08-01) drops, B and C (08-04) stay
    )
    assert {s.session_id[:1] for s in sessions} == {"b", "c"}


def test_defensive_parsing(tmp_path):
    root = tmp_path / "transcripts"
    root.mkdir()
    good = {
        "type": "assistant",
        "sessionId": "sid-1",
        "timestamp": "2026-08-01T10:00:00Z",
        "cwd": str(tmp_path),
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }
    sidechain = dict(good, sessionId="sid-side", isSidechain=True)
    no_timestamp = {"type": "assistant", "sessionId": "sid-2", "cwd": str(tmp_path)}
    wrong_cwd = dict(good, sessionId="sid-3", cwd="/somewhere/else")
    lines = [
        "{not json at all",
        json.dumps({"type": "queue-operation", "content": "x"}),
        json.dumps(sidechain),
        json.dumps(no_timestamp),
        json.dumps(wrong_cwd),
        json.dumps(good),
    ]
    (root / "mixed.jsonl").write_text("\n".join(lines), encoding="utf-8")

    sessions = transcripts.load_sessions(str(tmp_path), root, now=parse_iso8601("2026-08-02T00:00:00Z"), days=1)
    assert len(sessions) == 1
    assert sessions[0].session_id == "sid-1"


def test_relative_edited_files_drop_outside_paths(fixture_env):
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"],
        fixture_env["transcripts_root"],
        now=parse_iso8601(fixture_env["now"]),
        days=30,
    )
    transcripts.group_edit_files_by_repo(sessions, fixture_env["repo_cwd"])
    session_a = sessions[0]
    assert "app.py" in session_a.edited_files
    assert "tests/test_core.py" in session_a.edited_files
    assert all(not f.startswith("/") for f in session_a.edited_files)


def test_parse_iso8601_handles_z_and_naive():
    parsed = parse_iso8601("2026-08-01T10:00:00Z")
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    assert parse_iso8601("garbage") is None
    assert parse_iso8601("") is None


def test_normalize_command_collapses_whitespace():
    assert normalize_command("npm   test\n -q  ") == "npm test -q"
