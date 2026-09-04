"""Lens unit tests: retry chains, cache classes, verification statuses, survival classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yield_audit.events import ApiCall, Session, ToolResult, ToolUse
from yield_audit.lenses.cache_locality import analyze_cache_locality
from yield_audit.lenses.retry import analyze_retry
from yield_audit.lenses.survival import SurvivalUnit, classify_path
from yield_audit.lenses.verify_gap import analyze_verify_gap
from yield_audit.pricing import load_pricing

T0 = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


def make_session(name="s") -> Session:
    return Session(session_id=name, cwd="/repo", transcript_path="x", start=T0, end=T0)


def test_retry_ignores_repeats_without_errors():
    session = make_session()
    for i, (cmd, err) in enumerate([("npm test", False), ("npm test", False)]):
        use = ToolUse(id=f"u{i}", ts=T0 + timedelta(minutes=i), name="Bash", input={"command": cmd})
        session.tool_uses.append(use)
        session.tool_results[use.id] = ToolResult(use.id, use.ts, is_error=err)
    session.api_calls = [ApiCall(T0, "claude-sonnet-5", 100, 10, 0, 0)]

    result = analyze_retry(session)
    assert result.chains == []
    assert result.tax_tokens == 0


def test_retry_taxes_failure_chain_intervals():
    session = make_session()
    calls = []
    for i in range(4):
        ts = T0 + timedelta(minutes=i)
        calls.append(ApiCall(ts, "claude-sonnet-5", 100, 20, 0, 0))
    session.api_calls = calls
    plan = [("npm test", True), ("npm test", True), ("pytest -q", False)]
    for i, (cmd, err) in enumerate(plan):
        ts = T0 + timedelta(minutes=i + 1)
        use = ToolUse(id=f"u{i}", ts=ts, name="Bash", input={"command": cmd})
        session.tool_uses.append(use)
        session.tool_results[use.id] = ToolResult(use.id, use.ts, is_error=err)

    result = analyze_retry(session)
    assert len(result.chains) == 1
    chain = result.chains[0]
    assert chain.attempts == 2 and chain.errors == 2
    # api calls at :01 and :02 fall inside [first, last]; :00 and :03 do not
    assert result.taxed_api_calls == 2
    assert result.tax_token_share == 0.5


def test_cache_classes_compaction_vs_ttl_vs_prefix_break():
    table, fallback, _ = load_pricing()
    session = make_session()
    # t0: first call (excluded), t0+1m warm, t0+10m cold with boundary before it,
    # t0+20m cold after long gap, t0+21m cold with short gap
    boundary = T0 + timedelta(minutes=5)
    session.api_calls = [
        ApiCall(T0, "claude-sonnet-5", 100, 0, 0, 0),
        ApiCall(T0 + timedelta(minutes=1), "claude-sonnet-5", 0, 0, 200, 0),
        ApiCall(T0 + timedelta(minutes=10), "claude-sonnet-5", 300, 0, 0, 0),
        ApiCall(T0 + timedelta(minutes=20), "claude-sonnet-5", 400, 0, 0, 0),
        ApiCall(T0 + timedelta(minutes=21), "claude-sonnet-5", 500, 0, 0, 0),
    ]
    session.compact_boundaries = [boundary]

    result = analyze_cache_locality(session, table, fallback)
    classes = [e.class_name for e in result.events]
    assert classes == ["compaction", "ttl_expiry", "prefix_break"]
    assert result.by_class == {"compaction": 1, "ttl_expiry": 1, "prefix_break": 1}
    # compaction excluded from waste; ttl call (400 input) and prefix break (500) counted
    expected = (400 + 500) * (2.0 - 0.2) / 1_000_000
    assert result.wasted_usd == pytest.approx(expected)


def test_verify_status_transitions():
    sessions = []
    # gap: commits, no verify command
    s_gap = make_session("gap")
    s_gap.tool_uses.append(ToolUse("u", T0, "Bash", {"command": "git commit -m x"}))
    # verified before commit
    s_ok = make_session("ok")
    s_ok.tool_uses.append(ToolUse("v", T0, "Bash", {"command": "pytest -q"}))
    s_ok.tool_uses.append(ToolUse("c", T0 + timedelta(minutes=5), "Bash", {"command": "git commit -m x"}))
    # verified only after commit
    s_after = make_session("after")
    s_after.tool_uses.append(ToolUse("c2", T0, "Bash", {"command": "git commit -m x"}))
    s_after.tool_uses.append(ToolUse("v2", T0 + timedelta(minutes=5), "Bash", {"command": "cargo test"}))
    sessions = [s_gap, s_ok, s_after]

    commit_dates = {"gap": T0 + timedelta(minutes=1), "ok": T0 + timedelta(minutes=5), "after": T0 + timedelta(minutes=1)}
    result = analyze_verify_gap(sessions, commit_dates, {})
    assert result.sessions["gap"].status == "gap"
    assert result.sessions["ok"].status == "verified_before_commit"
    assert result.sessions["after"].status == "verified_after_commit"
    assert result.gap_rate == 1 / 3


def test_verify_correlation_reports_means():
    s_gap = make_session("gap")
    s_gap.tool_uses.append(ToolUse("u", T0, "Bash", {"command": "git commit -m x"}))
    s_ok = make_session("ok")
    s_ok.tool_uses.append(ToolUse("v", T0, "Bash", {"command": "make"}))
    result = analyze_verify_gap(
        [s_gap, s_ok],
        {"gap": T0, "ok": T0},
        {"gap": 0.2, "ok": 0.8},
    )
    assert result.correlation["gap"]["mean_survival"] == 0.2
    assert result.correlation["verified_before_commit"]["mean_survival"] == 0.8
    assert "correlation is observational" in result.notes[0]


def test_classify_path_kinds():
    assert classify_path("src/app.py") == "source"
    assert classify_path("tests/test_app.py") == "test"
    assert classify_path("src/app.test.ts") == "test"
    assert classify_path("README.md") == "docs"
    assert classify_path("docs/guide.rst") == "docs"
    assert classify_path("config/settings.yaml") == "config"
    assert classify_path("package-lock.json") == "config"


def test_survival_aggregate_weights_contested_commits_by_share():
    from yield_audit.lenses.survival import _aggregate

    # u1 fully owned (share 1.0, 10/10 survived); u2 contested at half share (0/10).
    # Weighted: (10 + 0) / (10 + 5) = 0.667 — unweighted would be 10/20 = 0.5.
    units = [
        SurvivalUnit("s1", "c", "a.py", "source", added=10, share=1.0, survived={7: 10}, deleted={7: False}),
        SurvivalUnit("s2", "c", "a.py", "source", added=10, share=0.5, survived={7: 0}, deleted={7: False}),
    ]
    result = _aggregate(units, horizons=(7,), headline_horizon=7, notes=[])
    assert result.overall == pytest.approx(10 / 15)
    assert result.sessions["s1"]["added"] == pytest.approx(10)
    assert result.sessions["s2"]["added"] == pytest.approx(5)


def test_survival_unit_classification_thresholds():
    unit_removed = SurvivalUnit("s", "c", "a.py", "source", added=10, survived={7: 0}, deleted={7: True})
    from yield_audit.lenses.waste import EDITED, REMOVED, REWRITTEN, classify_unit

    assert classify_unit(unit_removed, 7) == REMOVED
    unit_rewritten = SurvivalUnit("s", "c", "a.py", "source", added=10, survived={7: 5}, deleted={7: False})
    assert classify_unit(unit_rewritten, 7) == REWRITTEN
    unit_edited = SurvivalUnit("s", "c", "a.py", "source", added=10, survived={7: 6}, deleted={7: False})
    assert classify_unit(unit_edited, 7) == EDITED
    unit_pending = SurvivalUnit("s", "c", "a.py", "source", added=10, pending_horizons=[7])
    assert classify_unit(unit_pending, 7) is None


def test_verify_pattern_matches_common_commands():
    from yield_audit.lenses.verify_gap import VERIFY_PATTERN

    matches = [
        "pytest -q",
        "npm test",
        "npm run test:ci",
        "cargo test --all",
        "go test ./...",
        "make check",
        "swift build",
        "xcodebuild -scheme App test",
        "yarn test",
        "ruff check .",
        "./gradlew test",
    ]
    for command in matches:
        assert VERIFY_PATTERN.search(command), command
    non_matches = ["echo done", "git commit -m x", "ls -la"]
    for command in non_matches:
        assert not VERIFY_PATTERN.search(command), command
