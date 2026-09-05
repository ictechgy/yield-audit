"""M14 — incident origin cohorts (기획서 v0.3, local variant).

Question: when a fix/revert/rollback commit lands, whose lines was it
pointing at? For every fix-pattern commit F, blame each file F touched
once at F's parent and once at F: origin commits whose line counts drop
across F are the ones F rewrote or deleted. The drop-per-origin counts,
aggregated by cohort label, give the evidence-graded answer.

Measurement honesty: the drop arithmetic attributes *deleted and
rewritten* lines alike to their origins — a stated proxy for "lines the
incident pointed at", not an exact diff attribution. M13 (verification
tax transfer), by contrast, needs external CI data and stays deferred
behind the local-only contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..cohorts import COHORT_LABELS, HUMAN
from ..gitdata import CommitInfo, GitError, blame_sha_counts

FIX_SUMMARY_RE = re.compile(
    r"(?i)(?:^|\b)(?:fix|fixes|fixed|fixup|hotfix|bugfix|revert|reverts|reverted|rollback)\b"
)
MAX_ORIGIN_ROWS = 20


@dataclass
class IncidentResult:
    fix_commits: int
    targeted_lines_total: int
    by_cohort: dict[str, int]  # cohort label -> targeted line count
    origins: list[dict]  # [{commit, label, lines}] bounded, lines-desc then sha
    notes: list[str] = field(default_factory=list)


def analyze_incidents(
    repo: str,
    commits: list[CommitInfo],
    labels: dict[str, tuple[str, str]],
    *,
    blame_cache: dict | None = None,
) -> IncidentResult:
    cache = blame_cache if blame_cache is not None else {}
    fix_commits = 0
    by_cohort: dict[str, int] = {label: 0 for label in COHORT_LABELS}
    origin_totals: dict[str, int] = {}

    for commit in sorted(commits, key=lambda c: c.sha):
        if not FIX_SUMMARY_RE.search(commit.summary or ""):
            continue
        if not commit.files:
            continue
        fix_commits += 1
        parent_ref = f"{commit.sha}^"
        for path in sorted(commit.files):
            parent_key = (parent_ref, path)
            fix_key = (commit.sha, path)
            try:
                if parent_key not in cache:
                    cache[parent_key] = blame_sha_counts(repo, parent_ref, path)
                if fix_key not in cache:
                    cache[fix_key] = blame_sha_counts(repo, commit.sha, path)
            except GitError:
                continue  # path (or parent) does not exist at one of the refs
            for origin_sha, dropped in _dropped_lines(cache[parent_key], cache[fix_key]).items():
                if dropped <= 0:
                    continue
                label, _evidence = labels.get(origin_sha, (HUMAN, "no_ai_evidence"))
                by_cohort[label] = by_cohort.get(label, 0) + dropped
                origin_totals[origin_sha] = origin_totals.get(origin_sha, 0) + dropped

    origins = [
        {"commit": sha, "label": labels.get(sha, (HUMAN, "no_ai_evidence"))[0], "lines": lines}
        for sha, lines in sorted(origin_totals.items(), key=lambda item: (-item[1], item[0]))[:MAX_ORIGIN_ROWS]
    ]
    return IncidentResult(
        fix_commits=fix_commits,
        targeted_lines_total=sum(by_cohort.values()),
        by_cohort=by_cohort,
        origins=origins,
        notes=[
            "targeted lines = blame-count drops across fix/revert/rollback commits, attributed to the origin commits; deleted and rewritten lines count alike (proxy)",
            "cohort labels are evidence grades (certain/probable/human), not authorship verdicts",
        ],
    )


def _dropped_lines(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    dropped: dict[str, int] = {}
    for sha, count in before.items():
        delta = count - after.get(sha, 0)
        if delta > 0:
            dropped[sha] = delta
    return dropped
