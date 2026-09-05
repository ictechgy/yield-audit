"""M12 — cohort settle rate (long-horizon survival, 기획서 v0.3.1).

Question: is AI-marked code still there months later? For every commit,
survival = of the lines it added, how many are still verbatim at the
snapshot ``settle_days`` (default 90) later — the same blame semantics as
M1, aggregated by evidence cohort like M11, at a horizon long enough to
talk about settling rather than immediate rework.

The settle rate at a given horizon is the exact complement of the rework
rate at the same horizon; the two lenses exist separately because their
horizons differ by design (rework 14d, settle 90d) and both must stay
independently configurable.
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
class SettleResult:
    horizon_days: int
    evidence: dict[str, int]
    cohorts: dict[str, dict]  # label -> {commits, measured, pending, added, survived, rate}
    commits: list[dict]
    notes: list[str] = field(default_factory=list)


def _empty_bucket() -> dict:
    return {
        "commits": 0,
        "measured_commits": 0,
        "pending_commits": 0,
        "added": 0,
        "survived": 0,
        "rate": None,
    }


def analyze_settle(
    repo: str,
    commits: list[CommitInfo],
    labels: dict[str, tuple[str, str]],
    *,
    now: datetime,
    horizon_days: int = 90,
    blame_cache: dict | None = None,
    touch_since: datetime | None = None,
) -> SettleResult:
    notes = [
        "settle = lines the commit added that are still verbatim at the settle-horizon snapshot (git blame); renames/copies not followed",
        "settle rate is the complement of the rework rate at the same horizon; both lenses are reported because their horizons differ by design",
        "zero-addition commits (merges, empty changesets) are excluded from cohort counts entirely",
    ]
    if horizon_days <= 0:
        notes.append("disabled: settle horizon <= 0")
        return SettleResult(horizon_days=horizon_days, evidence={}, cohorts={}, commits=[], notes=notes)

    cache = blame_cache if blame_cache is not None else {}
    touches, merges = _touches(repo, cache, touch_since)
    cohorts: dict[str, dict] = {label: _empty_bucket() for label in REPORT_LABELS}
    evidence: dict[str, int] = {label: 0 for label in COHORT_LABELS}
    detail: list[dict] = []

    for commit in sorted(commits, key=lambda c: c.sha):
        label, evidence_text = labels.get(commit.sha, (HUMAN, "no_ai_evidence"))
        total_added = sum(commit.files.values())
        if total_added <= 0:
            continue
        bucket = cohorts[label]
        bucket["commits"] += 1
        evidence[label] = evidence.get(label, 0) + 1
        row = {
            "commit": commit.sha,
            "label": label,
            "evidence": evidence_text,
            "added": total_added,
            "survived": 0,
            "pending": False,
        }

        survived = 0
        pending = False
        target = commit.date + timedelta(days=horizon_days)
        if target > now:
            pending = True
        elif all(
            _untouched_between(touches, merges, commit, path, target)
            for path, added in commit.files.items()
            if added > 0
        ):
            survived = total_added  # provably untouched: everything still verbatim
        else:
            ref = _snapshot(repo, target, cache)
            tree = _tree(repo, ref, cache)
            for path, added in sorted(commit.files.items()):
                if added <= 0:
                    continue
                if path not in tree:
                    continue  # file deleted: nothing of it settled
                key = (ref, path)
                if key not in cache:
                    cache[key] = blame_sha_counts(repo, ref, path)
                survived += min(added, cache[key].get(commit.sha, 0))

        if pending:
            bucket["pending_commits"] += 1
            row["pending"] = True
        else:
            bucket["measured_commits"] += 1
            bucket["added"] += total_added
            bucket["survived"] += survived
            row["survived"] = survived
        detail.append(row)

    for bucket in cohorts.values():
        bucket["rate"] = (bucket["survived"] / bucket["added"]) if bucket["added"] else None

    combined = _empty_bucket()
    for label in (CERTAIN, PROBABLE):
        for key in ("commits", "measured_commits", "pending_commits", "added", "survived"):
            combined[key] += cohorts[label][key]
    combined["rate"] = (combined["survived"] / combined["added"]) if combined["added"] else None
    cohorts[AI_COMBINED] = combined

    return SettleResult(
        horizon_days=horizon_days,
        evidence=evidence,
        cohorts=cohorts,
        commits=detail,
        notes=notes,
    )
