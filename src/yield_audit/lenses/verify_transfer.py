"""M13 — verification-tax transfer (operator-provided CI export).

Question: generation got cheaper — did verification move to CI? For each
commit that has CI runs, the lens counts runs and non-passing runs,
aggregated by cohort: an AI-vs-human difference in runs-per-commit is the
verification-tax transfer, measured.

Network stance: yield-audit never fetches anything. The operator exports
CI data themselves and hands the file over::

    gh run list -R owner/repo --limit 500 \
        --json databaseId,headSha,conclusion,event,workflowName,createdAt \
        > ci-runs.json
    yield-audit audit --repo . --ci-runs ci-runs.json

Accepted shape: a JSON array of objects with at least ``headSha``;
``conclusion`` is gh's vocabulary (``success`` / ``failure`` /
``timed_out`` / ``startup_failure`` / ``cancelled`` / null while running).
A run counts as *not passing* when it completed with any conclusion other
than ``success``; in-progress runs (null) are excluded from both counts.
Entries without a usable ``headSha`` are skipped, counted as ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..cohorts import CERTAIN, COHORT_LABELS, HUMAN, PROBABLE
from ..gitdata import CommitInfo

AI_COMBINED = "ai_combined"
REPORT_LABELS = (*COHORT_LABELS, AI_COMBINED)
NOT_PASSING = {"failure", "timed_out", "startup_failure", "cancelled"}


@dataclass
class VerifyTransferResult:
    enabled: bool
    runs_total: int  # entries in the export
    runs_ignored: int  # entries without a usable headSha
    runs_joined: int  # entries whose headSha matched an in-window commit
    by_cohort: dict[str, dict]
    notes: list[str] = field(default_factory=list)


def parse_ci_runs(raw) -> list[dict]:
    """Validate an operator-provided export into (sha, passed, completed) rows."""
    if not isinstance(raw, list):
        raise ValueError("ci-runs export must be a JSON array (gh run list --json ...)")
    rows: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("headSha") or entry.get("head_sha")
        if not isinstance(sha, str) or not sha:
            continue
        conclusion = entry.get("conclusion")
        rows.append(
            {
                "sha": sha,
                "conclusion": conclusion,
                "workflow": entry.get("workflowName") if isinstance(entry.get("workflowName"), str) else "",
            }
        )
    return rows


def analyze_verify_transfer(
    commits: list[CommitInfo],
    labels: dict[str, tuple[str, str]],
    runs: list[dict] | None,
) -> VerifyTransferResult:
    notes = [
        "source: operator-provided CI export (yield-audit performs no network calls); runs join commits by headSha",
        "a completed run counts as not-passing when its conclusion is anything other than success (failure, timed_out, startup_failure, cancelled); in-progress (null) runs are excluded",
        "cohort labels are evidence grades, not authorship verdicts",
    ]
    if runs is None:
        return VerifyTransferResult(
            enabled=False, runs_total=0, runs_ignored=0, runs_joined=0,
            by_cohort={}, notes=["disabled: no --ci-runs file given"] + notes,
        )

    runs_by_sha: dict[str, list[str | None]] = {}
    for row in runs:
        runs_by_sha.setdefault(row["sha"], []).append(row["conclusion"])

    buckets: dict[str, dict] = {
        label: {"commits": 0, "runs": 0, "not_passing": 0, "runs_per_commit": None, "not_passing_rate": None}
        for label in REPORT_LABELS
    }
    joined = 0
    for commit in commits:
        conclusions = runs_by_sha.get(commit.sha)
        if not conclusions:
            continue
        label, _evidence = labels.get(commit.sha, (HUMAN, "no_ai_evidence"))
        bucket = buckets[label]
        bucket["commits"] += 1
        for conclusion in conclusions:
            joined += 1
            bucket["runs"] += 1
            if conclusion is not None and conclusion != "success":
                bucket["not_passing"] += 1

    for bucket in buckets.values():
        if bucket["commits"]:
            bucket["runs_per_commit"] = bucket["runs"] / bucket["commits"]
            bucket["not_passing_rate"] = (bucket["not_passing"] / bucket["runs"]) if bucket["runs"] else None

    combined = {"commits": 0, "runs": 0, "not_passing": 0, "runs_per_commit": None, "not_passing_rate": None}
    for label in (CERTAIN, PROBABLE):
        for key in ("commits", "runs", "not_passing"):
            combined[key] += buckets[label][key]
    if combined["commits"]:
        combined["runs_per_commit"] = combined["runs"] / combined["commits"]
        combined["not_passing_rate"] = (combined["not_passing"] / combined["runs"]) if combined["runs"] else None
    buckets[AI_COMBINED] = combined

    return VerifyTransferResult(
        enabled=True,
        runs_total=len(runs),
        runs_ignored=sum(len(v) for v in runs_by_sha.values()) - joined,
        runs_joined=joined,
        by_cohort=buckets,
        notes=notes,
    )
