"""Codex adapter tests: rollout-schema handling, namespacing, multi-agent e2e."""

from __future__ import annotations

import json

from conftest import (
    CODEX_PATCH,
    NOW,
    SESSION_D,
    codex_exec_call,
    codex_exec_call_output,
    codex_function_call,
    codex_function_call_output,
    codex_meta,
    codex_token_count,
    codex_turn_context,
    codex_write_file_call,
)
from yield_audit import transcripts
from yield_audit.cli import main
from yield_audit.events import parse_iso8601


def test_codex_sessions_are_namespaced(fixture_env, tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        codex_meta("2026-08-05T11:00:00Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:01Z", fixture_env["repo_cwd"]),
        codex_token_count("2026-08-05T11:00:30Z", {"input_tokens": 10, "output_tokens": 5}),
    ]
    (root / "rollout-x.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    assert [s.session_id for s in sessions] == [f"codex:{SESSION_D}"]


def test_codex_usage_tool_and_edit_extraction(fixture_env, tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        codex_meta("2026-08-05T11:00:00Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:01Z", fixture_env["repo_cwd"], model="gpt-5.1-codex"),
        codex_token_count(
            "2026-08-05T11:00:30Z",
            {"input_tokens": 120, "cached_input_tokens": 80, "output_tokens": 30},
        ),
        codex_function_call("2026-08-05T11:00:40Z", "c1", "shell", {"command": ["git", "commit", "-m", "x"]}),
        codex_function_call_output("2026-08-05T11:00:50Z", "c1", 0),
        codex_function_call(
            "2026-08-05T11:01:00Z", "c2", "apply_patch", {"input": CODEX_PATCH.format(cwd=fixture_env["repo_cwd"])}
        ),
        codex_function_call_output("2026-08-05T11:01:10Z", "c2", 0),
    ]
    (root / "rollout-x.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    session = sessions[0]
    assert len(session.api_calls) == 1
    call = session.api_calls[0]
    assert call.model == "gpt-5.1-codex"
    assert (call.input_tokens, call.cache_read_tokens, call.output_tokens) == (120, 80, 30)

    # shell tool normalized to Bash with a joined command; ran git commit detected
    assert [u.name for u in session.tool_uses] == ["Bash", "apply_patch"]
    assert session.tool_uses[0].input["command"] == "git commit -m x"
    assert session.ran_git_commit is True

    # apply_patch file headers land in edited_files (repo-relative after grouping)
    transcripts.group_edit_files_by_repo(sessions, fixture_env["repo_cwd"])
    assert session.edited_files == ["codex_notes.md"]

    # failed shell output (exit_code 1) is linked as an error
    assert session.tool_results["c1"].is_error is False


def test_codex_error_outputs_and_junk_records_are_skipped(fixture_env, tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        "{not json",
        json.dumps({"timestamp": "2026-08-05T11:00:00Z", "type": "event_msg", "payload": {}}),
        codex_meta("2026-08-05T11:00:05Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:06Z", fixture_env["repo_cwd"]),
        codex_token_count("2026-08-05T11:00:30Z", {"input_tokens": "junk", "output_tokens": -5}),
        codex_function_call("2026-08-05T11:00:40Z", "c1", "shell", {"command": "npm test"}),
        codex_function_call_output("2026-08-05T11:00:50Z", "c1", 3),
    ]
    (root / "rollout-x.jsonl").write_text(
        records[0] + "\n" + "".join(json.dumps(r) + "\n" for r in records[1:]), encoding="utf-8"
    )
    # a separate rollout for another project: its session_meta declares a
    # foreign cwd and the adapter stops reading the file right there
    (root / "rollout-foreign.jsonl").write_text(
        json.dumps(codex_meta("2026-08-05T12:00:00Z", "/somewhere/else")) + "\n"
        + json.dumps(codex_token_count("2026-08-05T12:00:30Z", {"input_tokens": 5, "output_tokens": 5})) + "\n",
        encoding="utf-8",
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    assert len(sessions) == 1
    session = sessions[0]
    assert session.api_calls[0].input_tokens == 0  # junk usage degrades to 0
    assert session.tool_results["c1"].is_error is True  # exit_code 3
    # retry lens sees the failed command
    assert [(cmd, err) for _, cmd, err in session.bash_sequence()] == [("npm test", True)]


def test_codex_date_layout_is_pruned_by_window(fixture_env, tmp_path):
    from conftest import build_codex_transcripts

    root = build_codex_transcripts(tmp_path, fixture_env["repo_cwd"])
    # cutoff 08-20 minus 16 days -> 08-04: the 08-05 rollout stays
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601("2026-08-20T00:00:00Z"), days=16, agents=("codex",)
    )
    assert len(sessions) == 1
    # cutoff 08-06: every day-dir (08-05) is pruned, nothing is even listed
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601("2026-08-07T00:00:00Z"), days=1, agents=("codex",)
    )
    assert sessions == []


def test_codex_early_exit_on_foreign_cwd(tmp_path, fixture_env):
    # the wrong-project rollout must be abandoned at its session_meta —
    # its token_count payload (which would crash parsing if read with a
    # stale ctx) proves the file was not read to the end
    root = tmp_path / "codex"
    root.mkdir()
    foreign = [
        codex_meta("2026-08-05T12:00:00Z", "/somewhere/else"),
        codex_token_count("2026-08-05T12:00:30Z", {"input_tokens": 5, "output_tokens": 5}),
    ]
    (root / "foreign.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in foreign), encoding="utf-8"
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    assert sessions == []


def test_cross_vendor_schema_pollution_is_harmless(fixture_env):
    # the claude fixture root parsed by the codex adapter (and vice versa)
    # yields only the owning vendor's sessions
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], fixture_env["transcripts_root"],
        now=parse_iso8601(NOW), days=30, agents=("claude", "codex"),
    )
    assert {s.session_id.split(":")[0] for s in sessions} == {"claude"}
    assert len(sessions) == 3


def test_multi_agent_e2e_report(fixture_env_multi, capsys):
    code = main(
        [
            "audit",
            "--repo", fixture_env_multi["repo_cwd"],
            "--transcripts-dir", str(fixture_env_multi["multi_root"]),
            "--now", NOW,
            "--format", "json",
        ]
    )
    assert code == 0
    import json as _json

    report = _json.loads(capsys.readouterr().out)
    assert report["parameters"]["agents_scanned"] == ["claude", "codex"]
    assert report["input"]["sessions"] == 4  # 3 claude + 1 codex
    # per-session keys carry the vendor namespace (m4 covers every session,
    # including the codex one whose edits were never committed)
    vendors = {key.split(":")[0] for key in report["m4_accepted"]["per_session"]}
    assert vendors == {"claude", "codex"}
    # codex session D: retry chain of pytest (1 error then success)
    chains = [c for c in report["m3_retry"]["failure_chains"] if c["session"].startswith("codex")]
    assert len(chains) == 1
    assert chains[0]["command"] == "pytest -q"
    assert chains[0]["attempts"] == 2 and chains[0]["errors"] == 1


def test_agent_flag_selects_single_vendor(fixture_env_multi, capsys):
    code = main(
        [
            "audit",
            "--repo", fixture_env_multi["repo_cwd"],
            "--transcripts-dir", str(fixture_env_multi["multi_root"]),
            "--now", NOW, "--agent", "codex", "--format", "json",
        ]
    )
    assert code == 0
    import json as _json

    report = _json.loads(capsys.readouterr().out)
    assert report["parameters"]["agents_scanned"] == ["codex"]
    assert report["input"]["sessions"] == 1


def test_unknown_agent_flag_fails_cleanly(fixture_env, capsys):
    code = main(
        ["audit", "--repo", fixture_env["repo_cwd"], "--now", NOW, "--agent", "nope"]
    )
    assert code == 2
    assert "unknown agent" in capsys.readouterr().err


def test_current_format_exec_and_status(fixture_env, tmp_path):
    """custom_tool_call (exec) — the format real rollouts use since 2026-09."""
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        codex_meta("2026-08-05T11:00:00Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:01Z", fixture_env["repo_cwd"]),
        codex_token_count(
            "2026-08-05T11:00:30Z",
            {"input_tokens": 100, "cached_input_tokens": 60,
             "cache_write_input_tokens": 40, "output_tokens": 20},
        ),
        codex_exec_call("2026-08-05T11:00:40Z", "x1", "pytest -q"),
        codex_exec_call_output("2026-08-05T11:00:50Z", "x1"),
        codex_exec_call("2026-08-05T11:01:00Z", "x2", "pytest -q", status="failed"),
        codex_exec_call_output("2026-08-05T11:01:10Z", "x2"),
        codex_exec_call("2026-08-05T11:01:20Z", "x3", "git commit -m x"),
        codex_exec_call_output("2026-08-05T11:01:30Z", "x3"),
    ]
    (root / "rollout-new.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    session = sessions[0]
    # raw exec input becomes the Bash command; commit detection works
    assert [(u.input.get("command"), session.tool_results[u.id].is_error) for u in session.tool_uses] == [
        ("pytest -q", False),
        ("pytest -q", True),  # status != completed marks the error
        ("git commit -m x", False),
    ]
    assert session.ran_git_commit is True
    # cache_write_input_tokens now flows through (was hardcoded 0)
    assert session.api_calls[0].cache_write_tokens == 40


def test_current_format_write_file_maps_to_edit(fixture_env, tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        codex_meta("2026-08-05T11:00:00Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:01Z", fixture_env["repo_cwd"]),
        codex_write_file_call("2026-08-05T11:00:40Z", "w1", fixture_env["repo_cwd"] + "/notes.md"),
    ]
    (root / "rollout-w.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    sessions = transcripts.load_sessions(
        fixture_env["repo_cwd"], root, now=parse_iso8601(NOW), days=30, agents=("codex",)
    )
    transcripts.group_edit_files_by_repo(sessions, fixture_env["repo_cwd"])
    assert sessions[0].edited_files == ["notes.md"]
