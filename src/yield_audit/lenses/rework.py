"""M11 — AI rework rate (cohort comparison, 기획서 v0.3.0).

Question: how quickly is an AI-marked commit reworked compared to a human
one? For a commit C, "reworked" means lines C added that no longer exist
verbatim at the snapshot taken ``horizon_days`` after C — removed, or
rewritten by a later commit (revert, hotfix, refactor). This is the
complement of M1's blame-based survival, applied to *every* commit and
aggregated by cohort label (:mod:`yield_audit.cohorts`) instead of session.

Semantics shared with M1 (same limits, same honesty):

- blame snapshot at ``commit.date + horizon_days``; renames/copies are not
  followed, a renamed file counts as fully reworked;
- commits whose horizon has not yet elapsed are ``pending`` — excluded
  from the rate, counted honestly;
- a deleted file counts as fully reworked.

Pan-judgment principle: the lens reports a measured difference between
cohorts in this repo and window; it never concludes that AI code is worse
(or better).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..cohorts import CERTAIN, COHORT_LABELS, HUMAN, PROBABLE
from ..gitdata import CommitInfo, blame_sha_counts
from .survival import _snapshot, _touches, _tree, _untouched_between

AI_COMBINED = "ai_combined"
REPORT_LABELS = (*COHORT_LABELS, AI_COMBINED)


@dataclass
class ReworkResult:
    horizon_days: int
    evidence: dict[str, int]  # label -> commit count (every labeled commit)
    cohorts: dict[str, dict]  # label -> {commits, measured, pending, added, reworked, rate}
    commits: list[dict]  # per-commit detail, sha-sorted (report bounds it)
    notes: list[str] = field(default_factory=list)


def _empty_bucket() -> dict:
    return {"commits": 0, "measured_commits": 0, "pending_commits": 0, "added": 0, "reworked": 0, "rate": None}


def analyze_rework(
    repo: str,
    commits: list[CommitInfo],
    labels: dict[str, tuple[str, str]],
    *,
    now: datetime,
    horizon_days: int = 14,
    blame_cache: dict | None = None,
    touch_since: datetime | None = None,
) -> ReworkResult:
    """Rework rate per cohort over ``horizon_days``.

    ``labels`` comes from :func:`yield_audit.cohorts.label_commits`; a commit
    missing from it (defensive: label and message windows should match)
    degrades to ``human``. ``horizon_days`` <= 0 disables the measurement
    and returns an empty, honestly-labeled result.
    """
    notes = [
        "rework = lines the commit added that are no longer present verbatim at the horizon snapshot (git blame); renames/copies not followed",
        "cohort labels are evidence grades, not authorship verdicts: certain = AI footer in message, probable = agent session joined by time window + edited files, human = neither",
    ]
    if horizon_days <= 0:
        notes.append("disabled: rework horizon <= 0")
        return ReworkResult(horizon_days=horizon_days, evidence={}, cohorts={}, commits=[], notes=notes)

    cache = blame_cache if blame_cache is not None else {}
    touches, merges = _touches(repo, cache, touch_since)
    cohorts: dict[str, dict] = {label: _empty_bucket() for label in REPORT_LABELS}
    evidence: dict[str, int] = {label: 0 for label in COHORT_LABELS}
    detail: list[dict] = []

    for commit in sorted(commits, key=lambda c: c.sha):
        label, evidence_text = labels.get(commit.sha, (HUMAN, "no_ai_evidence"))
        bucket = cohorts[label]
        bucket["commits"] += 1
        evidence[label] = evidence.get(label, 0) + 1
        total_added = sum(commit.files.values())
        row = {
            "commit": commit.sha,
            "label": label,
            "evidence": evidence_text,
            "added": total_added,
            "reworked": 0,
            "pending": False,
        }

        reworked = 0
        pending = total_added <= 0
        if total_added > 0:
            target = commit.date + timedelta(days=horizon_days)
            if target > now:
                pending = True
            elif all(
                _untouched_between(touches, merges, commit, path, target)
                for path, added in commit.files.items()
                if added > 0
            ):
                # No commit (and no merge) changed any of the commit's
                # paths inside the window: nothing can have been reworked,
                # provable without a single blame process.
                pass
            else:
                ref = _snapshot(repo, target, cache)
                tree = _tree(repo, ref, cache)
                for path, added in sorted(commit.files.items()):
                    if added <= 0:
                        continue
                    if path not in tree:  # file deleted since: everything reworked
                        reworked += added
                        continue
                    key = (ref, path)
                    if key not in cache:
                        cache[key] = blame_sha_counts(repo, ref, path)
                    survived = cache[key].get(commit.sha, 0)
                    reworked += max(0, added - survived)

        if pending:
            bucket["pending_commits"] += 1
            row["pending"] = True
        else:
            bucket["measured_commits"] += 1
            bucket["added"] += total_added
            bucket["reworked"] += reworked
            row["reworked"] = reworked
        detail.append(row)

    for bucket in cohorts.values():
        bucket["rate"] = (bucket["reworked"] / bucket["added"]) if bucket["added"] else None

    # AI view = certain + probable; heuristics included, honestly merged.
    combined = _empty_bucket()
    for label in (CERTAIN, PROBABLE):
        for key in ("commits", "measured_commits", "pending_commits", "added", "reworked"):
            combined[key] += cohorts[label][key]
    combined["rate"] = (combined["reworked"] / combined["added"]) if combined["added"] else None
    cohorts[AI_COMBINED] = combined

    return ReworkResult(
        horizon_days=horizon_days,
        evidence=evidence,
        cohorts=cohorts,
        commits=detail,
        notes=notes,
    )
