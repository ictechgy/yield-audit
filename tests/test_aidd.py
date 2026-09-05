"""End-to-end aidd: fixture repo split between C1/C2 and C3/C4.

Timeline (conftest): before-period (window .. 2026-08-05T00:00Z) holds
C1 (probable — session A ran it) and C2 (human); after-period
(2026-08-05 .. now=08-20) holds C3 (certain footer) and C4 (human,
reworks 6 of C3's 10 feature.md lines; C4 itself pending at 14d).
"""

from __future__ import annotations

import json

import pytest

from conftest import NOW
from yield_audit.cli import main


def aidd_argv(fixture_env, *extra) -> list[str]:
    return [
        "aidd",
        "--repo", fixture_env["repo_cwd"],
        "--transcripts-dir", str(fixture_env["transcripts_root"]),
        "--split", "2026-08-05T00:00:00Z",
        "--days", "60",
        "--now", NOW,
        *extra,
    ]


@pytest.fixture
def aidd(fixture_env, capsys) -> dict:
    assert main([*aidd_argv(fixture_env), "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_schema_and_windows(aidd):
    assert aidd["schema_version"] == "yieldaudit.aidd.v1"
    assert aidd["parameters"]["split"].startswith("2026-08-05")
    before, after = aidd["periods"]["before"], aidd["periods"]["after"]
    assert before["window_end"].startswith("2026-08-05")
    assert before["commits"] == 2  # C1, C2
    assert after["commits"] == 2  # C3, C4
    assert before["sessions"] == 3  # all claude sessions end before the split
    assert after["sessions"] == 0


def test_cohort_tables(aidd):
    before, after = aidd["periods"]["before"], aidd["periods"]["after"]
    assert before["cohort_evidence"] == {"certain": 0, "probable": 1, "human": 1}
    assert before["cohorts"]["probable"]["reworked_lines"] == 11  # C1 golden
    assert after["cohort_evidence"] == {"certain": 1, "probable": 0, "human": 1}
    assert after["cohorts"]["certain"]["rework_rate"] == pytest.approx(0.6)
    assert after["cohorts"]["human"]["pending_commits"] == 1  # C4 horizon pending


def test_comparison_block(aidd):
    comp = aidd["comparison"]
    assert comp["ai_rework_rate"]["after"] == pytest.approx(0.6)
    # the after-period human cohort is only C4, whose 14d horizon (08-23)
    # has not elapsed at now=08-20: unmeasurable, honestly None
    assert comp["human_rework_rate"]["after"] is None
    assert comp["ai_vs_human_rework_ratio"]["after"] is None
    assert comp["ai_rework_rate"]["before"] == pytest.approx(11 / 24)
    # before: human C2 has 0 rework -> ratio None there as well
    assert comp["ai_vs_human_rework_ratio"]["before"] is None


def test_console_and_markdown_render(fixture_env, capsys):
    assert main([*aidd_argv(fixture_env), "--format", "console"]) == 0
    out = capsys.readouterr().out
    assert "AI vs human ratio" in out
    assert "[before]" in out and "[after]" in out
    assert "evidence: certain=1" in out

    assert main([*aidd_argv(fixture_env), "--format", "markdown"]) == 0
    md = capsys.readouterr().out
    assert "# yield-audit aidd report" in md
    assert "## after: AI rework by cohort" in md


def test_aidd_rejects_future_split(fixture_env, capsys):
    code = main([*aidd_argv(fixture_env, "--split", "2027-01-01T00:00:00Z", "--now", NOW)])
    assert code == 2
    assert "future" in capsys.readouterr().err
