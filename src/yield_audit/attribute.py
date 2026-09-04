"""Session-to-commit attribution with explicit confidence grades.

Attribution is probabilistic by nature, so every pair carries a grade and the
report states it. Rules (v0.1, deterministic):

- Candidates: sessions overlapping the commit date (session must start no more
  than ``grace`` after the commit, and end no earlier than ``window`` before
  it) that share at least one edited file with the commit.
- ``high``  — the session itself ran ``git commit`` AND shares edited files.
- ``medium``— time proximity + shared edited files.
- A commit claimed by several same-grade sessions is split evenly across them
  and flagged ambiguous; higher grades win over lower grades regardless of
  overlap counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .events import Session
from .gitdata import CommitInfo

GRADES = ("high", "medium", "none")


@dataclass
class Attribution:
    session_id: str
    commit_sha: str
    grade: str
    share: float  # 1.0, or 1/n when contested by equal-grade sessions
    shared_files: list[str]


@dataclass
class AttributionResult:
    pairs: list[Attribution]
    ambiguous_commits: list[str]
    claimed_shas: set[str]
    unclaimed_commits: list[str]

    def for_session(self, session_id: str) -> list[Attribution]:
        return [p for p in self.pairs if p.session_id == session_id]


def attribute(
    sessions: list[Session],
    commits: list[CommitInfo],
    *,
    proximity: timedelta = timedelta(hours=24),
    start_grace: timedelta = timedelta(minutes=5),
) -> AttributionResult:
    # Commit files as sets, sessions' edited files as sets.
    commit_files = {c.sha: set(c.files) for c in commits}
    session_files = {s.session_id: set(s.edited_files) for s in sessions}

    claims: dict[str, list[tuple[str, str, int]]] = {}  # commit_sha -> [(session_id, grade, overlap)]
    for commit in commits:
        for session in sessions:
            edited = session_files[session.session_id]
            if not edited:
                continue
            overlap = edited & commit_files[commit.sha]
            if not overlap:
                continue
            if session.start > commit.date + start_grace:
                continue
            if session.end < commit.date - proximity:
                continue
            grade = "high" if session.ran_git_commit else "medium"
            claims.setdefault(commit.sha, []).append(
                (session.session_id, grade, len(overlap))
            )

    pairs: list[Attribution] = []
    ambiguous: list[str] = []
    for commit in commits:
        claim_list = claims.get(commit.sha, [])
        if not claim_list:
            continue
        best_grade = min((g for _, g, _ in claim_list), key=GRADES.index)
        winners = [(sid, grade, ov) for sid, grade, ov in claim_list if grade == best_grade]
        # Highest overlap first; session_id as a deterministic tiebreaker.
        winners.sort(key=lambda item: (-item[2], item[0]))
        top_overlap = winners[0][2]
        tied = [w for w in winners if w[2] == top_overlap]
        if len(tied) > 1 and best_grade != "high":
            ambiguous.append(commit.sha)
            share = 1.0 / len(tied)
        else:
            tied = tied[:1]
            share = 1.0
        for sid, grade, _ov in tied:
            pairs.append(
                Attribution(
                    session_id=sid,
                    commit_sha=commit.sha,
                    grade=grade,
                    share=share,
                    shared_files=sorted(edited & commit_files[commit.sha]),
                )
            )

    pairs.sort(key=lambda p: (p.commit_sha, p.session_id))
    claimed = {p.commit_sha for p in pairs}
    unclaimed = [c.sha for c in commits if c.sha not in claimed]
    return AttributionResult(
        pairs=pairs,
        ambiguous_commits=sorted(ambiguous),
        claimed_shas=claimed,
        unclaimed_commits=unclaimed,
    )


def commits_by_sha(commits: list[CommitInfo]) -> dict[str, CommitInfo]:
    return {c.sha: c for c in commits}
