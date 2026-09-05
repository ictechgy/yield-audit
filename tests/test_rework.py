"""M11 rework lens + cohort labeling tests (fixture repo, golden values).

Fixture timeline (conftest): C1 @08-01 (probable — session A ran it),
C2 @08-04 human, C3 @08-05 certain (footer, feature.md 10 lines),
C4 @08-09 human (rewrites 6 of those 10), C5 @08-10 human fix commit
(rewrites 2 of C4's lines; its own horizon is pending). Default now 2026-08-20:

- C1: added 24, survived at 14d = 13 -> reworked 11 (rate 11/24)
- C2: added 13, untouched -> reworked 0
- C3: added 10, C4 rewrote 6 -> reworked 6 (rate 0.6)
- C4: 14d horizon (08-23) > now -> pending
"""

from __future__ import annotations

import pytest

from conftest import NOW
from yield_audit import cohorts, gitdata
from yield_audit.events import parse_iso8601
from yield_audit.lenses.rework import analyze_rework


@pytest.fixture
def rework_env(fixture_env):
    repo = str(fixture_env["repo"])
    now = parse_iso8601(NOW)
    warnings: list[str] = []
    commits = gitdata.commits_with_numstat(repo, since=None, until=None, warnings=warnings)
    messages = gitdata.commit_messages(repo, since=None, until=None)
    by_summary = {c.summary: c.sha for c in commits}
    assert set(by_summary) == {"c1: initial work", "c2: follow-up", "c3: add feature notes", "c4: rework feature", "fix: correct feature notes"}
    return {
        "repo": repo,
        "now": now,
        "commits": commits,
        "messages": messages,
        "sha": by_summary,
    }


def test_footer_evidence_patterns():
    assert cohorts.footer_evidence("x\n\nCo-Authored-By: Claude <n@anthropic.com>") == "claude"
    assert cohorts.footer_evidence("Generated with Claude Code") == "claude"
    assert cohorts.footer_evidence("🤖 Generated with Codex") == "codex"
    assert cohorts.footer_evidence("docs: typo\n\nCo-authored-by: GitHub Copilot") == "copilot"
    assert cohorts.footer_evidence("plain commit") is None
    assert cohorts.footer_evidence("") is None


def test_label_commits_evidence_ordering(rework_env):
    claimed = {rework_env["sha"]["c1: initial work"]}
    labels = cohorts.label_commits(rework_env["messages"], claimed)
    assert labels[rework_env["sha"]["c3: add feature notes"]][0] == "certain"
    assert labels[rework_env["sha"]["c3: add feature notes"]][1] == "footer:claude"
    # session join without footer -> probable; neither -> human
    assert labels[rework_env["sha"]["c1: initial work"]][0] == "probable"
    assert labels[rework_env["sha"]["c2: follow-up"]][0] == "human"
    # footer beats session match
    both = cohorts.label_commits(
        {rework_env["sha"]["c1: initial work"]: "Co-Authored-By: Claude"}, claimed
    )
    assert both[rework_env["sha"]["c1: initial work"]][0] == "certain"


def test_rework_golden_at_14d(rework_env):
    claimed = {rework_env["sha"]["c1: initial work"]}
    labels = cohorts.label_commits(rework_env["messages"], claimed)
    result = analyze_rework(
        rework_env["repo"], rework_env["commits"], labels, now=rework_env["now"], horizon_days=14
    )
    assert result.evidence == {"certain": 1, "probable": 1, "human": 3}

    certain = result.cohorts["certain"]
    assert certain["added"] == 10 and certain["reworked"] == 6
    assert certain["rate"] == pytest.approx(0.6)
    assert certain["pending_commits"] == 0

    probable = result.cohorts["probable"]
    assert probable["added"] == 24 and probable["reworked"] == 11
    assert probable["rate"] == pytest.approx(11 / 24)

    human = result.cohorts["human"]
    assert human["added"] == 13 and human["reworked"] == 0
    assert human["rate"] == 0.0
    # C4 and C5 horizons (08-23, 08-24) have not elapsed at now=08-20
    assert human["pending_commits"] == 2

    combined = result.cohorts["ai_combined"]
    assert combined["added"] == 34 and combined["reworked"] == 17
    assert combined["rate"] == pytest.approx(0.5)

    by_sha = {row["commit"]: row for row in result.commits}
    assert by_sha[rework_env["sha"]["c4: rework feature"]]["pending"] is True
    assert by_sha[rework_env["sha"]["c1: initial work"]]["reworked"] == 11


def test_rework_pending_when_horizon_not_elapsed(rework_env):
    labels = cohorts.label_commits(rework_env["messages"], set())
    result = analyze_rework(
        rework_env["repo"], rework_env["commits"], labels, now=rework_env["now"], horizon_days=30
    )
    # 30d from every commit lands after now=08-20: nothing measurable
    assert all(bucket["pending_commits"] == bucket["commits"] for bucket in result.cohorts.values())
    assert all(bucket["rate"] is None for bucket in result.cohorts.values())


def test_rework_disabled_at_zero(rework_env):
    labels = cohorts.label_commits(rework_env["messages"], set())
    result = analyze_rework(
        rework_env["repo"], rework_env["commits"], labels, now=rework_env["now"], horizon_days=0
    )
    assert result.cohorts == {} and result.evidence == {} and result.commits == []
    assert any("disabled" in note for note in result.notes)
