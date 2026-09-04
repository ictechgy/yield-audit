"""Attribution rules: grades, contest splitting, time-window exclusions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yield_audit.attribute import attribute
from yield_audit.events import Session
from yield_audit.gitdata import CommitInfo

T0 = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


def make_session(session_id: str, start: datetime, end: datetime, files, ran_commit: bool = False) -> Session:
    return Session(
        session_id=session_id,
        cwd="/repo",
        transcript_path="x.jsonl",
        start=start,
        end=end,
        edited_files=list(files),
        ran_git_commit=ran_commit,
    )


def make_commit(sha: str, date: datetime, files: dict[str, int]) -> CommitInfo:
    return CommitInfo(sha=sha, date=date, summary="s", files=files)


def test_high_grade_when_session_ran_the_commit():
    sessions = [make_session("s1", T0 - timedelta(hours=1), T0 + timedelta(minutes=30), ["app.py"], ran_commit=True)]
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute(sessions, commits)
    assert len(result.pairs) == 1
    assert result.pairs[0].grade == "high"
    assert result.pairs[0].share == 1.0
    assert result.ambiguous_commits == []


def test_medium_grade_without_commit_command():
    sessions = [make_session("s1", T0 - timedelta(hours=1), T0 - timedelta(minutes=30), ["app.py"])]
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute(sessions, commits)
    assert result.pairs[0].grade == "medium"


def test_session_ending_before_window_is_excluded():
    sessions = [make_session("s1", T0 - timedelta(days=3), T0 - timedelta(days=2), ["app.py"])]
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute(sessions, commits)
    assert result.pairs == []
    assert result.unclaimed_commits == ["c1"]


def test_session_starting_after_commit_is_excluded():
    sessions = [make_session("s1", T0 + timedelta(hours=2), T0 + timedelta(hours=3), ["app.py"])]
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute(sessions, commits)
    assert result.pairs == []


def test_no_file_overlap_is_excluded():
    sessions = [make_session("s1", T0 - timedelta(hours=1), T0, ["other.py"])]
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute(sessions, commits)
    assert result.pairs == []


def test_high_beats_medium_regardless_of_overlap_size():
    low = make_session("s-low", T0 - timedelta(hours=2), T0, ["app.py", "b.py", "c.py", "d.py"])
    high = make_session("s-high", T0 - timedelta(hours=1), T0, ["app.py"], ran_commit=True)
    commits = [make_commit("c1", T0, {"app.py": 10, "b.py": 1, "c.py": 1, "d.py": 1})]

    result = attribute([low, high], commits)
    assert len(result.pairs) == 1
    assert result.pairs[0].session_id == "s-high"
    assert result.pairs[0].grade == "high"


def test_contested_medium_claims_are_split_and_flagged():
    s1 = make_session("s1", T0 - timedelta(hours=1), T0 - timedelta(minutes=30), ["app.py"])
    s2 = make_session("s2", T0 - timedelta(hours=1), T0 - timedelta(minutes=30), ["app.py"])
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute([s1, s2], commits)
    assert result.ambiguous_commits == ["c1"]
    assert sorted(p.session_id for p in result.pairs) == ["s1", "s2"]
    assert all(p.share == 0.5 for p in result.pairs)


def test_high_grade_is_not_split_even_when_tied():
    s1 = make_session("s1", T0 - timedelta(hours=1), T0, ["app.py"], ran_commit=True)
    s2 = make_session("s2", T0 - timedelta(hours=1), T0, ["app.py"], ran_commit=True)
    commits = [make_commit("c1", T0, {"app.py": 10})]

    result = attribute([s1, s2], commits)
    assert result.ambiguous_commits == []
    assert len(result.pairs) == 1  # deterministic winner: lowest session id
    assert result.pairs[0].session_id == "s1"
