"""Blame prefilter: untouched paths must never spawn a blame process.

The touch map (one ``git log --name-only`` pass) proves a path unchanged
inside a measurement window; survival/rework then assert the result
directly. These tests monkeypatch ``gitdata.blame_sha_counts`` to raise so
any accidental blame call fails loudly, and exercise the merge guard with
a real merge commit.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from conftest import _git, _write
from yield_audit import gitdata
from yield_audit.attribute import Attribution, AttributionResult
from yield_audit.cohorts import label_commits
from yield_audit.events import parse_iso8601
from yield_audit.lenses.rework import analyze_rework
from yield_audit.lenses.survival import _untouched_between, analyze_survival


def _commit_date(repo: Path, ref: str = "HEAD") -> datetime:
    out = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%aI", ref],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return parse_iso8601(out)


def _build_untouched_repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _write(repo, "a.py", "a1\na2\na3\n")
    _write(repo, "docs/guide.md", "g1\ng2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "c1", date="2026-08-01T10:00:00+00:00")
    return repo


def _attribution(repo: Path) -> tuple[AttributionResult, dict]:
    commits = gitdata.commits_with_numstat(str(repo), since=None, until=None)
    by_sha = {c.sha: c for c in commits}
    assert len(commits) == 1
    sha = commits[0].sha
    result = AttributionResult(
        pairs=[Attribution(session_id="s1", commit_sha=sha, grade="high", share=1.0, shared_files=["a.py"])],
        ambiguous_commits=[],
        claimed_shas={sha},
        unclaimed_commits=[],
    )
    return result, by_sha


def test_survival_skips_blame_for_untouched_paths(tmp_path, monkeypatch):
    repo = _build_untouched_repo(tmp_path)
    attributions, by_sha = _attribution(repo)
    monkeypatch.setattr(
        gitdata, "blame_sha_counts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("blame must not run for untouched paths")),
    )
    result = analyze_survival(
        str(repo), attributions, by_sha,
        now=parse_iso8601("2026-08-20T00:00:00Z"), horizons=(7,), headline_horizon=7,
    )
    # no commit after c1 exists: every added line survives, no blame spawned
    assert result.overall_survived == 5
    assert result.overall == 1.0
    assert all(u.deleted[7] is False for u in result.units)


def test_rework_skips_blame_for_untouched_paths(tmp_path, monkeypatch):
    repo = _build_untouched_repo(tmp_path)
    commits = gitdata.commits_with_numstat(str(repo), since=None, until=None)
    messages = gitdata.commit_messages(str(repo), since=None, until=None)
    labels = label_commits(messages, set())
    monkeypatch.setattr(
        gitdata, "blame_sha_counts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("blame must not run for untouched paths")),
    )
    result = analyze_rework(str(repo), commits, labels, now=parse_iso8601("2026-08-20T00:00:00Z"), horizon_days=14)
    human = result.cohorts["human"]
    assert human["added"] == 5 and human["reworked"] == 0 and human["rate"] == 0.0


def test_touched_paths_still_blame(tmp_path):
    # fixture repo: C2 touches app.py/notes.md/config.yaml after C1 — the
    # prefilter must defer those to real blame (golden values unchanged)
    from conftest import build_repo

    repo = build_repo(tmp_path)
    commits = gitdata.commits_with_numstat(str(repo), since=None, until=None)
    by_summary = {c.summary: c for c in commits}
    c1 = by_summary["c1: initial work"]
    touches, merges = gitdata.path_touch_log(str(repo), since=None, until=None)
    assert not merges
    target = c1.date + timedelta(days=7)
    assert not _untouched_between(touches, merges, c1, "app.py", target)
    assert not _untouched_between(touches, merges, c1, "notes.md", target)
    assert _untouched_between(touches, merges, c1, "tests/test_core.py", target)


def test_merge_commit_forces_blame(tmp_path):
    repo = _build_untouched_repo(tmp_path)

    # side branch touching an unrelated file, merged back after c1
    _git(repo, "checkout", "-b", "side")
    _write(repo, "other.py", "o1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "side", date="2026-08-03T10:00:00+00:00")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "side", "-m", "merge side", date="2026-08-04T10:00:00+00:00")

    commits = gitdata.commits_with_numstat(str(repo), since=None, until=None)
    c1 = next(c for c in commits if c.summary == "c1")
    touches, merges = gitdata.path_touch_log(str(repo), since=None, until=None)
    assert merges, "the merge commit must be reported"
    target = c1.date + timedelta(days=7)
    # even untouched-looking paths fall back to blame inside a merge window
    assert not _untouched_between(touches, merges, c1, "a.py", target)


def test_path_touch_log_uses_committer_dates(tmp_path):
    repo = _build_untouched_repo(tmp_path)
    # second commit with an OLD author date but a recent committer date
    _write(repo, "a.py", "a1\na2\na3\na4\n")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="Fixture",
        GIT_AUTHOR_EMAIL="fixture@example.com",
        GIT_COMMITTER_NAME="Fixture",
        GIT_COMMITTER_EMAIL="fixture@example.com",
        GIT_AUTHOR_DATE="2026-07-01T10:00:00+00:00",
        GIT_COMMITTER_DATE="2026-08-05T10:00:00+00:00",
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-am", "c2"],
        check=True, capture_output=True, text=True, env=env,
    )
    touches, _ = gitdata.path_touch_log(str(repo), since=None, until=None)
    dates = [d for d, _ in touches["a.py"]]
    assert parse_iso8601("2026-07-01T10:00:00Z") not in dates  # author date ignored
    assert any(d == parse_iso8601("2026-08-05T10:00:00Z") for d in dates)
