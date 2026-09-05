"""End-to-end: fixture repo + fixture transcripts -> golden metric values.

Every number asserted here follows deterministically from the fixture design
documented in conftest.py:

- Session A survival at 7d: app.py 5/10, notes.md 0/4 (deleted),
  config.yaml 2/4, tests/test_core.py 6/6 -> 13/24.
- Session A cost: sonnet-5 list prices over 4 observed api calls -> $0.00643.
- Session B retry tax: npm test failed twice -> 2 of 4 api calls taxed (0.5).
- Session C cold call is post-compaction -> excluded from cache waste.
"""

from __future__ import annotations

import json

import pytest

from conftest import NOW, SESSION_A, SESSION_B
from yield_audit.cli import main


def sid(raw: str) -> str:
    # report keys carry the vendor namespace since the adapter refactor
    return f"claude:{raw[:8]}"


def audit_argv(fixture_env, *extra) -> list[str]:
    return [
        "audit",
        "--repo",
        fixture_env["repo_cwd"],
        "--transcripts-dir",
        str(fixture_env["transcripts_root"]),
        "--now",
        NOW,
        *extra,
    ]


@pytest.fixture
def report(fixture_env, capsys) -> dict:
    code = main([*audit_argv(fixture_env), "--format", "json", "--details"])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_report_schema(report):
    assert report["schema_version"] == "yieldaudit.report.v1"
    assert report["input"]["sessions"] == 3
    assert report["input"]["commits_in_window"] == 4
    assert report["input"]["attributed_commits"] == 1
    assert report["input"]["unclaimed_commits"] == 3


def test_attribution_grades(report):
    assert report["attribution"]["grades"] == {"high": 1}
    assert report["attribution"]["ambiguous_commits"] == []


def test_m1_survival_golden(report):
    m1 = report["m1_survival"]
    assert m1["overall_rate"] == pytest.approx(13 / 24)
    assert m1["added_lines"] == 24
    assert m1["pending_units"] == 0
    kinds = m1["by_kind"]
    assert kinds["source"]["rate"] == pytest.approx(0.5)
    assert kinds["test"]["rate"] == pytest.approx(1.0)
    assert kinds["docs"]["rate"] == 0.0
    assert kinds["config"]["rate"] == pytest.approx(0.5)
    per_session = m1["per_session"][sid(SESSION_A)]
    assert per_session["rate"] == pytest.approx(13 / 24)
    assert per_session["pending"] == 0
    # per-unit details: notes.md was deleted before the 7d snapshot
    unit_paths = {u["path"]: u for u in m1["units"]}
    assert unit_paths["notes.md"]["deleted"] is True
    assert unit_paths["notes.md"]["survived"] == 0
    assert unit_paths["app.py"]["survived"] == 5
    assert unit_paths["app.py"]["kind"] == "source"
    # paths are redacted to basenames by default
    assert all("/" not in u["path"] for u in m1["units"])


def test_m2_waste_bounds_golden(report):
    m2 = report["m2_waste"]
    session_block = m2["per_session"][sid(SESSION_A)]
    session_cost = 0.00643
    assert session_block["lower_usd"] == pytest.approx(session_cost * (4 / 24), abs=1e-6)
    assert session_block["upper_usd"] == pytest.approx(session_cost * (18 / 24), abs=1e-6)
    assert session_block["removed_lines"] == 4
    assert session_block["rewritten_lines"] == 14  # app.py 10 + config.yaml 4
    assert session_block["edited_lines"] == 6  # tests/test_core.py
    assert m2["total_lower_usd"] == pytest.approx(session_cost * (4 / 24), abs=1e-6)


def test_m3_retry_tax_golden(report):
    m3 = report["m3_retry"]
    # session B: 2 taxed calls x 120 tokens; grand total across sessions = 4000
    assert m3["total_tax_tokens"] == 240
    assert m3["total_tokens"] == 4000
    assert m3["tax_share"] == pytest.approx(240 / 4000)
    assert len(m3["failure_chains"]) == 1
    chain = m3["failure_chains"][0]
    assert chain["attempts"] == 2
    assert chain["errors"] == 2
    assert chain["command"] == "npm test"
    # within session B alone the tax is half of everything
    assert m3["per_session"][sid(SESSION_B)]["tax_share"] == pytest.approx(0.5)


def test_m4_accepted_golden(report):
    m4 = report["m4_accepted"]
    assert m4["totals"]["accepted"]["sessions"] == 1
    assert m4["totals"]["no_output"]["sessions"] == 2
    assert m4["totals"]["rejected"]["sessions"] == 0
    assert m4["cost_per_accepted_usd"] == pytest.approx(0.00643, abs=1e-6)
    assert m4["per_session"][sid(SESSION_A)]["status"] == "accepted"
    assert m4["per_session"][sid(SESSION_A)]["survival_rate"] == pytest.approx(13 / 24)


def test_m5_cache_locality_golden(report):
    m5 = report["m5_cache"]
    assert m5["cold_calls"] == 5
    assert m5["cold_by_class"] == {"ttl_expiry": 1, "prefix_break": 3, "compaction": 1}
    # ttl: 890 tokens x 1.8/MTok + prefix breaks: 3 x 100 x 1.8/MTok
    expected = (890 + 3 * 100) * 1.8 / 1_000_000
    assert m5["wasted_usd"] == pytest.approx(expected, abs=1e-6)
    # compaction event must not be listed among schedulable waste events
    assert all(e["class"] != "compaction" for e in m5["events"])
    assert m5["mean_session_hit_rate"] is not None


def test_m8_verify_gap_golden(report):
    m8 = report["m8_verify"]
    assert m8["gap_rate"] == 0.0  # the only committing session verified first
    assert m8["gap_rate_strict"] == 0.0
    correlation = m8["correlation_with_survival"]
    assert correlation["verified_before_commit"]["mean_survival"] == pytest.approx(13 / 24, abs=1e-3)


def test_m11_rework_golden(report):
    m11 = report["m11_rework"]
    assert m11["measurement"] == "measured_from_git_history"
    assert m11["rework_horizon_days"] == 14
    # evidence grades ship with every percentage (판정 아님 원칙)
    assert m11["cohort_evidence"] == {"certain": 1, "probable": 1, "human": 2}
    cohorts = m11["cohorts"]
    assert cohorts["certain"]["reworked_lines"] == 6
    assert cohorts["certain"]["rework_rate"] == pytest.approx(0.6)
    assert cohorts["probable"]["reworked_lines"] == 11
    assert cohorts["probable"]["rework_rate"] == pytest.approx(11 / 24)
    assert cohorts["human"]["reworked_lines"] == 0
    assert cohorts["human"]["pending_commits"] == 1
    assert cohorts["ai_combined"]["rework_rate"] == pytest.approx(0.5)
    # per-commit detail rows exist under --details and carry evidence strings
    rows = {row["label"]: row for row in m11["commits"]}
    assert rows["certain"]["evidence"] == "footer:claude"
    assert rows["probable"]["evidence"] == "session_join:time_window+files"


def test_rework_days_flag_disables_m11(fixture_env, capsys):
    code = main([*audit_argv(fixture_env), "--format", "json", "--rework-days", "0"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["parameters"]["rework_horizon_days"] == 0
    assert data["m11_rework"]["cohorts"] == {}


def test_report_redacts_home_and_commands(report, fixture_env):
    # transcripts_root goes through home abbreviation; chain commands must not leak paths
    from yield_audit.redact import abbreviate_home

    assert report["parameters"]["transcripts_root"] == abbreviate_home(str(fixture_env["transcripts_root"]))
    for chain in report["m3_retry"]["failure_chains"]:
        assert not chain["command"].startswith("/")


def test_report_has_no_escape_bytes_anywhere(report):
    # deep-sanitize safety net: no transcript-derived string may carry
    # control or escape bytes into any format
    def walk(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from walk(key)
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for text in walk(report):
        assert "\x1b" not in text, repr(text)
        assert "\x07" not in text, repr(text)


def test_show_paths_flag_unredacts(report, fixture_env, capsys):
    code = main([*audit_argv(fixture_env), "--format", "json", "--details", "--show-paths"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    unit_paths = [u["path"] for u in data["m1_survival"]["units"]]
    assert any("/" in p for p in unit_paths)


def test_console_and_markdown_formats(fixture_env, capsys):
    base = audit_argv(fixture_env)
    assert main([*base, "--format", "console"]) == 0
    console_out = capsys.readouterr().out
    assert "M1 output survival" in console_out
    assert "54.2%" in console_out

    assert main([*base, "--format", "markdown"]) == 0
    markdown_out = capsys.readouterr().out
    assert "# yield-audit report" in markdown_out
    assert "| kind | survival | lines |" in markdown_out


def test_out_file_writes_json(fixture_env, tmp_path):
    out = tmp_path / "report.json"
    code = main([*audit_argv(fixture_env), "--out", str(out), "--format", "console"])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "yieldaudit.report.v1"


def test_horizon_30_fully_measured_later(fixture_env, capsys):
    argv = [
        "audit",
        "--repo",
        fixture_env["repo_cwd"],
        "--transcripts-dir",
        str(fixture_env["transcripts_root"]),
        "--now",
        "2026-10-01T00:00:00Z",
        "--days",
        "90",
        "--horizon",
        "30",
        "--horizons",
        "30",
        "--format",
        "json",
        "--details",
    ]
    code = main(argv)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    # at 30d everything is measurable; session A's units are unchanged (C2 is unclaimed)
    assert data["m1_survival"]["overall_rate"] == pytest.approx(13 / 24)
    assert data["m1_survival"]["horizon_days"] == 30
    assert data["m1_survival"]["pending_units"] == 0


def test_non_git_repo_fails_cleanly(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code = main(["audit", "--repo", str(plain), "--now", NOW])
    assert code == 2
    assert "not a git repository" in capsys.readouterr().err


def test_doctor_reports_environment(fixture_env, capsys):
    code = main(
        [
            "doctor",
            "--repo",
            fixture_env["repo_cwd"],
            "--transcripts-dir",
            str(fixture_env["transcripts_root"]),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "git on PATH: True" in out
    assert "sessions with cwd == repo: 3" in out
