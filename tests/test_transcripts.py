"""Transcript adapter tests: schema handling and defensive parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from yield_audit import transcripts
from yield_audit.events import normalize_command, parse_iso8601

Infinity = float("inf")


def test_load_fixture_sessions(fixture_env):
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"],
        fixture_env["transcripts_root"],
        now=parse_iso8601(fixture_env["now"]),
        days=30,
    )
    assert [s.session_id.split(":")[1][:1] for s in sessions] == ["a", "b", "c"]

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
    assert {s.session_id.split(":")[1][:1] for s in sessions} == {"b", "c"}


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
    assert sessions[0].session_id == "claude:sid-1"


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


def test_float_usage_tokens_are_truncated_not_zeroed(tmp_path):
    root = tmp_path / "transcripts"
    root.mkdir()
    record = {
        "type": "assistant",
        "sessionId": "sid-float",
        "timestamp": "2026-08-01T10:00:00Z",
        "cwd": str(tmp_path),
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [],
            "usage": {
                "input_tokens": 100.7,
                "output_tokens": 20.2,
                "cache_read_input_tokens": 300.0,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    (root / "float.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    sessions = transcripts.load_sessions(str(tmp_path), root, now=parse_iso8601("2026-08-02T00:00:00Z"), days=1)
    call = sessions[0].api_calls[0]
    assert (call.input_tokens, call.output_tokens, call.cache_read_tokens) == (100, 20, 300)


def test_infinite_usage_tokens_do_not_crash(tmp_path):
    # Python's json accepts bare Infinity; int(inf) must never be attempted.
    root = tmp_path / "transcripts"
    root.mkdir()
    line = json.dumps({
        "type": "assistant",
        "sessionId": "sid-inf",
        "timestamp": "2026-08-01T10:00:00Z",
        "cwd": str(tmp_path),
        "message": {"role": "assistant", "model": "m", "content": [],
                    "usage": {"output_tokens": Infinity}},
    }).replace('"Infinity"', "Infinity")  # bare token, as a hostile file would have
    (root / "inf.jsonl").write_text(line + "\n", encoding="utf-8")
    sessions = transcripts.load_sessions(str(tmp_path), root, now=parse_iso8601("2026-08-02T00:00:00Z"), days=1)
    assert sessions[0].api_calls[0].output_tokens == 0


def test_munged_name_is_separator_free_even_on_windows_paths():
    name = transcripts.munged_project_dir_name("C:\\Users\\me\\repo")
    assert "/" not in name and "\\" not in name and ":" not in name
    assert name == "C--Users-me-repo"
    # POSIX layout unchanged
    assert transcripts.munged_project_dir_name("/Users/me/repo") == "-Users-me-repo"


def test_munged_directory_prefilter_and_fallback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    repo_cwd = str(repo.resolve())
    root = tmp_path / "projects"
    munged = root / repo_cwd.replace("/", "-")
    munged.mkdir(parents=True)
    other = root / "-somewhere-else"
    other.mkdir()

    def write_session(directory: Path, name: str) -> None:
        record = {
            "type": "assistant",
            "sessionId": f"sid-{name}",
            "timestamp": "2026-08-01T10:00:00Z",
            "cwd": repo_cwd if name != "wrong" else "/elsewhere",
            "message": {"role": "assistant", "model": "m", "content": [], "usage": {}},
        }
        (directory / f"{name}.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    write_session(munged, "right")
    write_session(other, "wrong")

    # prefilter: munged dir exists -> its sessions are found without walking elsewhere
    sessions = transcripts.load_sessions(repo_cwd, root, now=parse_iso8601("2026-08-02T00:00:00Z"), days=1)
    assert [s.session_id for s in sessions] == ["claude:sid-right"]

    # fallback: root without a munged dir degrades to a full walk (finds nothing here)
    empty_root = tmp_path / "empty-projects"
    empty_root.mkdir()
    sessions = transcripts.load_sessions(repo_cwd, empty_root, now=parse_iso8601("2026-08-02T00:00:00Z"), days=1)
    assert sessions == []


def test_parse_iso8601_handles_z_and_naive():
    parsed = parse_iso8601("2026-08-01T10:00:00Z")
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    assert parse_iso8601("garbage") is None
    assert parse_iso8601("") is None


def test_normalize_command_collapses_whitespace():
    assert normalize_command("npm   test\n -q  ") == "npm test -q"
