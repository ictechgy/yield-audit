"""Shared fixtures: a deterministic git repository + synthetic transcripts.

Dates are pinned with GIT_*_DATE so survival horizons and attribution are
reproducible. Transcripts mirror the observed Claude Code JSONL schema (key
names only; content payloads are dummies).

Timeline (all UTC):

- Session A: 2026-08-01 09:55 – 10:04:30, edits app.py / notes.md /
  config.yaml / tests/test_core.py, runs ``pytest`` (verification) then
  ``git commit`` -> commit C1 @ 08-01 10:00 (10+4+4+6 added lines).
  One cold API call after a >5min gap (ttl_expiry class).
- Commit C2 @ 2026-08-04 10:00 (unattributed follow-up): rewrites 5 of
  app.py's 10 lines, adds tests/test_app.py, deletes notes.md, rewrites
  2 of config.yaml's 4 lines.
- Commit C3 @ 2026-08-05 12:00 (M11 certain cohort): adds feature.md
  (10 lines) with an AI footer (``Co-Authored-By: Claude``). Untouched by
  sessions -> M1/attribution goldens unchanged.
- Commit C4 @ 2026-08-09 09:00 (M11 human): rewrites 6 of feature.md's
  10 lines. C3's 14d rework snapshot is 08-19 <= now -> C3 rework 6/10;
  C4's own horizon (08-23) is pending at the default now.
- Session B: 2026-08-04 10:00 – 10:03, repeats a failing ``npm test``
  (retry-tax chain), then runs ``pytest`` once; no edits, no commits.
- Session C: 2026-08-04 10:10 – 10:16, one compaction boundary followed by
  a cold call (compaction class, excluded from waste); no commits.
- Codex session D (separate rollout-format fixture, only used by the
  codex/multi-agent tests): 2026-08-05 11:00 – 11:02, shell retry chain
  (pytest fails once then passes) and one apply_patch editing
  codex_notes.md (never committed -> no attribution interference).

Default "now" for audits: 2026-08-20T00:00:00Z -> C1 and C2 are measurable
at the 7d horizon, everything is pending at 30d.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_CWD = None  # set by fixture (realpath of the repo)
NOW = "2026-08-20T00:00:00Z"

SESSION_A = "aaaaaaaa-0000-4000-8000-000000000001"
SESSION_B = "bbbbbbbb-0000-4000-8000-000000000002"
SESSION_C = "cccccccc-0000-4000-8000-000000000003"


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
    )
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def build_repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")

    c1_date = "2026-08-01T10:00:00+00:00"
    _write(repo, "app.py", "".join(f"alpha line {i}\n" for i in range(1, 11)))
    _write(repo, "notes.md", "".join(f"note {i}\n" for i in range(1, 5)))
    _write(repo, "config.yaml", "key_a: 1\nkey_b: 2\nkey_c: 3\nkey_d: 4\n")
    _write(repo, "tests/test_core.py", "".join(f"def test_core_{i}(): pass\n" for i in range(1, 7)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c1: initial work", date=c1_date)

    c2_date = "2026-08-04T10:00:00+00:00"
    _write(repo, "app.py", "".join(f"beta line {i}\n" for i in range(1, 6)) + "".join(f"alpha line {i}\n" for i in range(6, 11)))
    _write(repo, "tests/test_app.py", "".join(f"def test_app_{i}(): pass\n" for i in range(1, 7)))
    (repo / "notes.md").unlink()
    _write(repo, "config.yaml", "key_a: 1\nkey_b: 2b\nkey_c: 3\nkey_d: 4d\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c2: follow-up", date=c2_date)

    c3_date = "2026-08-05T12:00:00+00:00"
    _write(repo, "feature.md", "".join(f"feature line {i}\n" for i in range(1, 11)))
    _git(repo, "add", "-A")
    _git(
        repo,
        "commit",
        "-m", "c3: add feature notes",
        "-m", "Co-Authored-By: Claude <noreply@anthropic.com>",
        date=c3_date,
    )

    c4_date = "2026-08-09T09:00:00+00:00"
    _write(
        repo,
        "feature.md",
        "".join(f"feature v2 line {i}\n" for i in range(1, 7))
        + "".join(f"feature line {i}\n" for i in range(7, 11)),
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c4: rework feature", date=c4_date)
    return repo


def assistant_record(session_id: str, ts: str, cwd: str, usage, tool_uses=(), model="claude-sonnet-5") -> dict:
    content: list[dict] = [{"type": "text", "text": "ok"}]
    for tool_id, name, tool_input in tool_uses:
        content.append({"type": "tool_use", "id": tool_id, "name": name, "input": tool_input})
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": cwd,
        "isSidechain": False,
        "message": {
            "role": "assistant",
            "id": "msg_" + session_id[:8] + ts.replace(":", "").replace("-", ""),
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": usage[0],
                "output_tokens": usage[1],
                "cache_read_input_tokens": usage[2],
                "cache_creation_input_tokens": usage[3],
            },
        },
    }


def user_results_record(session_id: str, ts: str, cwd: str, results) -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": cwd,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error, "content": "out"}
                for tool_id, is_error in results
            ],
        },
    }


def system_record(session_id: str, ts: str, cwd: str, subtype: str) -> dict:
    return {
        "type": "system",
        "subtype": subtype,
        "sessionId": session_id,
        "timestamp": ts,
        "cwd": cwd,
    }


def build_transcripts(base: Path, repo_cwd: str) -> Path:
    root = base / "transcripts" / "-tmp-fixture"
    root.mkdir(parents=True)

    # --- session A ---
    a: list[dict] = [
        assistant_record(SESSION_A, "2026-08-01T09:55:00Z", repo_cwd, (200, 100, 0, 300)),
        assistant_record(SESSION_A, "2026-08-01T09:56:00Z", repo_cwd, (100, 50, 400, 0)),
        assistant_record(
            SESSION_A,
            "2026-08-01T09:58:00Z",
            repo_cwd,
            (50, 50, 600, 0),
            tool_uses=[("toolu_a1", "Bash", {"command": "pytest -q"})],
        ),
        user_results_record(SESSION_A, "2026-08-01T09:58:20Z", repo_cwd, [("toolu_a1", False)]),
        assistant_record(
            SESSION_A,
            "2026-08-01T10:04:00Z",
            repo_cwd,
            (90, 60, 0, 800),
            tool_uses=[
                ("toolu_a2", "Write", {"file_path": repo_cwd + "/app.py", "content": "..."}),
                ("toolu_a3", "Write", {"file_path": repo_cwd + "/notes.md", "content": "..."}),
                ("toolu_a4", "Write", {"file_path": repo_cwd + "/config.yaml", "content": "..."}),
                ("toolu_a5", "Write", {"file_path": repo_cwd + "/tests/test_core.py", "content": "..."}),
                ("toolu_a6", "Bash", {"command": "git commit -am 'c1'"}),
            ],
        ),
        user_results_record(
            SESSION_A,
            "2026-08-01T10:04:30Z",
            repo_cwd,
            [("toolu_a2", False), ("toolu_a3", False), ("toolu_a4", False), ("toolu_a5", False), ("toolu_a6", False)],
        ),
    ]

    # --- session B: retry tax, no commits ---
    b: list[dict] = [
        assistant_record(SESSION_B, "2026-08-04T10:00:00Z", repo_cwd, (100, 20, 0, 0)),
        assistant_record(
            SESSION_B,
            "2026-08-04T10:01:00Z",
            repo_cwd,
            (100, 20, 0, 0),
            tool_uses=[("toolu_b1", "Bash", {"command": "npm  test"})],
        ),
        user_results_record(SESSION_B, "2026-08-04T10:01:10Z", repo_cwd, [("toolu_b1", True)]),
        assistant_record(
            SESSION_B,
            "2026-08-04T10:02:00Z",
            repo_cwd,
            (100, 20, 0, 0),
            tool_uses=[("toolu_b2", "Bash", {"command": "npm test"})],
        ),
        user_results_record(SESSION_B, "2026-08-04T10:02:10Z", repo_cwd, [("toolu_b2", True)]),
        assistant_record(
            SESSION_B,
            "2026-08-04T10:03:00Z",
            repo_cwd,
            (100, 20, 0, 0),
            tool_uses=[("toolu_b3", "Bash", {"command": "pytest -q"})],
        ),
        user_results_record(SESSION_B, "2026-08-04T10:03:10Z", repo_cwd, [("toolu_b3", False)]),
    ]

    # --- session C: compaction-then-cold, no commits ---
    c: list[dict] = [
        assistant_record(SESSION_C, "2026-08-04T10:10:00Z", repo_cwd, (100, 10, 0, 500)),
        system_record(SESSION_C, "2026-08-04T10:15:00Z", repo_cwd, "compact_boundary"),
        assistant_record(SESSION_C, "2026-08-04T10:16:00Z", repo_cwd, (100, 10, 0, 0)),
    ]

    for session_id, records in ((SESSION_A, a), (SESSION_B, b), (SESSION_C, c)):
        with (root / f"{session_id}.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
    return root


@pytest.fixture
def fixture_env(tmp_path):
    repo = build_repo(tmp_path)
    repo_cwd = str(repo.resolve())
    transcripts_root = build_transcripts(tmp_path, repo_cwd)
    return {"repo": repo, "repo_cwd": repo_cwd, "transcripts_root": transcripts_root, "now": NOW}


# --- Codex CLI rollout-format fixtures ---------------------------------

SESSION_D = "dddddddd-0000-4000-8000-000000000004"


def codex_meta(ts: str, cwd: str, session_id: str = SESSION_D) -> dict:
    return {
        "timestamp": ts,
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": cwd, "originator": "codex_cli_rs", "cli_version": "0.5.0"},
    }


def codex_turn_context(ts: str, cwd: str, model: str = "gpt-5.1-codex") -> dict:
    return {"timestamp": ts, "type": "turn_context", "payload": {"cwd": cwd, "model": model}}


def codex_token_count(ts: str, last_usage: dict) -> dict:
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {"last_token_usage": last_usage}},
    }


def codex_function_call(ts: str, call_id: str, name: str, arguments) -> dict:
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments},
    }


def codex_function_call_output(ts: str, call_id: str, exit_code: int) -> dict:
    return {
        "timestamp": ts,
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps({"output": "ran", "metadata": {"exit_code": exit_code}}),
        },
    }


CODEX_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: {cwd}/codex_notes.md\n"
    "@@\n"
    "-old\n"
    "+new\n"
    "*** End Patch\n"
)


def build_codex_transcripts(base: Path, repo_cwd: str) -> Path:
    """One rollout file mirroring the codex-rs JSONL layout (sessions/YYYY/MM/DD)."""
    root = base / "codex-sessions" / "2026" / "08" / "05"
    root.mkdir(parents=True)
    records = [
        codex_meta("2026-08-05T11:00:00Z", repo_cwd),
        codex_turn_context("2026-08-05T11:00:01Z", repo_cwd),
        codex_token_count("2026-08-05T11:00:30Z", {"input_tokens": 120, "cached_input_tokens": 80, "output_tokens": 30}),
        codex_function_call("2026-08-05T11:00:40Z", "call_d1", "shell", {"command": ["pytest", "-q"]}),
        codex_function_call_output("2026-08-05T11:00:50Z", "call_d1", 1),
        codex_token_count("2026-08-05T11:01:00Z", {"input_tokens": 150, "cached_input_tokens": 100, "output_tokens": 40}),
        codex_function_call("2026-08-05T11:01:10Z", "call_d2", "shell", {"command": ["pytest", "-q"]}),
        codex_function_call_output("2026-08-05T11:01:20Z", "call_d2", 0),
        codex_token_count("2026-08-05T11:01:40Z", {"input_tokens": 90, "cached_input_tokens": 0, "output_tokens": 60}),
        codex_function_call("2026-08-05T11:01:50Z", "call_d3", "apply_patch", {"input": CODEX_PATCH.format(cwd=repo_cwd)}),
        codex_function_call_output("2026-08-05T11:02:00Z", "call_d3", 0),
    ]
    rollout = root / f"rollout-2026-08-05T11-00-00-{SESSION_D}.jsonl"
    with rollout.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return root.parents[2]  # .../codex-sessions


@pytest.fixture
def fixture_env_multi(fixture_env, tmp_path):
    """Claude + Codex transcripts for the same repo, under one parent dir.

    The parent holds both vendors' layouts so ``--agent auto`` with a single
    ``--transcripts-dir`` scans both (adapters skip records that are not
    their vendor's schema).
    """
    parent = tmp_path / "all-transcripts"
    parent.mkdir()
    claude_root = parent / "claude"
    codex_root = parent / "codex"
    claude_root.mkdir()
    codex_root.mkdir()
    for file in fixture_env["transcripts_root"].rglob("*.jsonl"):
        shutil.copy2(file, claude_root / file.name)
    for file in build_codex_transcripts(tmp_path, fixture_env["repo_cwd"]).rglob("*.jsonl"):
        target = codex_root / file.relative_to(file.parents[3])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
    env = dict(fixture_env)
    env["multi_root"] = parent
    return env
