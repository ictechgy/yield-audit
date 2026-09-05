"""Perfetto export tests: Session → Agent Trace IR mapping, CLI e2e, soft dep."""

from __future__ import annotations

import json
import sys

import pytest

from conftest import (
    NOW,
    codex_function_call,
    codex_function_call_output,
    codex_meta,
    codex_token_count,
    codex_turn_context,
)

pytest.importorskip("agent2perfetto", reason="perfetto extra not installed")

from yield_audit import export  # noqa: E402
from yield_audit.audit import AuditError  # noqa: E402
from yield_audit.cli import main  # noqa: E402
from yield_audit.events import ApiCall, Session, ToolResult, ToolUse, parse_iso8601  # noqa: E402


def _ts(value: str):
    return parse_iso8601(value)


def _session() -> Session:
    s = Session(
        session_id="claude:s1",
        cwd="/repo",
        transcript_path="/t/s1.jsonl",
        start=_ts("2026-08-01T09:55:00Z"),
        end=_ts("2026-08-01T09:58:00Z"),
    )
    s.api_calls = [
        ApiCall(_ts("2026-08-01T09:55:00Z"), "m1", input_tokens=100, output_tokens=40,
                cache_read_tokens=200, cache_write_tokens=50),
        ApiCall(_ts("2026-08-01T09:57:00Z"), "m1", input_tokens=30, output_tokens=10,
                cache_read_tokens=400, cache_write_tokens=0),
    ]
    s.tool_uses = [
        ToolUse("toolu_1", _ts("2026-08-01T09:55:30Z"), "Bash", {"command": "ls"}),
    ]
    s.tool_results = {
        "toolu_1": ToolResult("toolu_1", _ts("2026-08-01T09:56:00Z"), is_error=True),
    }
    return s


def test_sessions_to_perfetto_slices_and_counters():
    trace = export.sessions_to_perfetto([_session()])
    events = trace["traceEvents"]
    assert trace["metadata"]["source"] == "yield-audit-export"
    names = [e["args"]["name"] for e in events if e["ph"] == "M" and e["name"] == "process_name"]
    assert names == ["agent session claude:s1"]

    turns = [e for e in events if e.get("cat") == "turn"]
    # 2 api calls + 1 tool use model_call = 3 "assistant turn" slices
    assert len(turns) == 3
    models = sorted(t["args"]["model"] for t in turns if t["args"]["model"])
    assert models == ["m1", "m1"]

    tools = [e for e in events if e.get("cat") == "tool_call"]
    assert [(e["name"], e["args"]["tool_use_id"]) for e in tools] == [("Bash", "toolu_1")]
    results = [e for e in events if e.get("cat") == "tool_result"]
    assert results[0]["args"]["is_error"] is True
    assert results[0]["name"] == "result Bash"
    # flow arrows pair the tool call with its result
    assert {e["ph"] for e in events if e["ph"] in ("s", "f")} == {"s", "f"}

    # counter lanes: ctx_* per call, spend_* cumulative (cache_write → cache_create)
    counters = [(e["ts"], e["args"]) for e in events if e["ph"] == "C"]
    base = _ts("2026-08-01T09:55:00Z").timestamp()
    first_ts = int((_ts("2026-08-01T09:55:00Z").timestamp() - base) * 1_000_000)
    second_ts = int((_ts("2026-08-01T09:57:00Z").timestamp() - base) * 1_000_000)
    assert [ts for ts, _ in counters] == [first_ts, second_ts]
    assert counters[0][1]["ctx_total"] == 350  # 100 + 200 + 50
    assert counters[0][1]["ctx_cache_create"] == 50
    assert counters[1][1]["spend_total"] == 780  # 130 + 600 + 50
    assert counters[1][1]["spend_cache_read"] == 600


def test_sessions_to_perfetto_multi_session_processes():
    other = _session()
    other.session_id = "codex:s2"
    trace = export.sessions_to_perfetto([_session(), other])
    names = [e["args"]["name"] for e in trace["traceEvents"]
             if e["ph"] == "M" and e["name"] == "process_name"]
    assert names == ["agent session claude:s1", "agent session codex:s2"]


def test_export_cli_e2e(fixture_env, tmp_path, capsys):
    root = tmp_path / "codex"
    root.mkdir()
    records = [
        codex_meta("2026-08-05T11:00:00Z", fixture_env["repo_cwd"]),
        codex_turn_context("2026-08-05T11:00:01Z", fixture_env["repo_cwd"]),
        codex_token_count("2026-08-05T11:00:30Z", {"input_tokens": 120, "cached_input_tokens": 80, "output_tokens": 30}),
        codex_function_call("2026-08-05T11:00:40Z", "c1", "shell", {"command": ["ls"]}),
        codex_function_call_output("2026-08-05T11:00:50Z", "c1", 0),
    ]
    (root / "rollout-x.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    out = tmp_path / "trace.perfetto.json"
    rc = main([
        "export", "--perfetto",
        "--repo", fixture_env["repo_cwd"],
        "--transcripts-dir", str(root),
        "--agent", "codex",
        "--days", "30",
        "--now", NOW,
        "--out", str(out),
    ])
    assert rc == 0
    trace = json.loads(out.read_text(encoding="utf-8"))
    assert trace["metadata"]["source"] == "yield-audit-export"
    cats = [e.get("cat") for e in trace["traceEvents"]]
    assert "turn" in cats and "tool_call" in cats and "tool_result" in cats
    counters = [e["args"] for e in trace["traceEvents"] if e["ph"] == "C"]
    assert counters[0]["ctx_input"] == 120
    assert counters[0]["ctx_cache_read"] == 80
    assert "exported 1 session(s)" in capsys.readouterr().out


def test_export_requires_a_format_flag(fixture_env, tmp_path, capsys):
    rc = main([
        "export",
        "--repo", fixture_env["repo_cwd"],
        "--transcripts-dir", str(fixture_env["transcripts_root"]),
        "--now", NOW,
        "--out", str(tmp_path / "x.json"),
    ])
    assert rc == 2
    assert "--perfetto" in capsys.readouterr().err


def test_export_missing_dependency_message(monkeypatch):
    # block the package AND the submodules earlier tests already imported
    for name in ("agent2perfetto", "agent2perfetto.ir", "agent2perfetto.trace"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(AuditError, match="perfetto"):
        export.sessions_to_perfetto([_session()])


def test_export_empty_sessions_still_writes_valid_trace():
    trace = export.sessions_to_perfetto([])
    assert trace["traceEvents"] == []
    assert trace["metadata"]["source"] == "yield-audit-export"
