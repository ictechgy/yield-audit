"""The `aidd` report: AI-transition (AIDD) cohort comparison.

Runs the standard audit pipeline over two windows split at a transition
date — before (``split - days .. split``) and after (``split .. now``) —
and renders the cohort comparison the 기획서 calls the one-line sell:
"AI rework rate was X% vs human Y% after the transition (Zx)".

Pan-judgment principle (판정 아님): the report states measured differences
for this repository in these windows, with cohort evidence distributions
attached. It never concludes that AI code is better or worse.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit, redact

AIDD_SCHEMA_VERSION = "yieldaudit.aidd.v1"


def run_aidd(
    *,
    repo: str,
    transcripts_root: Path | None,
    split: datetime,
    days: int,
    now: datetime,
    horizons: tuple[int, ...],
    headline_horizon: int,
    pricing_override: str | None,
    proximity_hours: int,
    rework_days: int,
    agents=None,
    use_cache: bool = True,
    log=None,
) -> dict:
    if split > now:
        raise audit.AuditError(f"--split is in the future: {split.isoformat()}")
    if days <= 0:
        raise audit.AuditError("--days must be > 0 for aidd (the per-period lookback)")

    def run(label: str, since: datetime, until: datetime | None) -> dict:
        report = audit.run_audit(
            repo=repo,
            transcripts_root=transcripts_root,
            days=days,
            horizons=horizons,
            headline_horizon=headline_horizon,
            now=now,
            pricing_override=pricing_override,
            proximity_hours=proximity_hours,
            show_paths=False,
            details=True,
            agents=agents,
            rework_days=rework_days,
            since=since,
            until=until,
            use_cache=use_cache,
            log=log,
        )
        return _period(label, report)

    before = run("before", split - timedelta(days=days), split)
    after = run("after", split, None)

    return redact.deep_sanitize(
        {
            "schema_version": AIDD_SCHEMA_VERSION,
            "generated_at": now.astimezone(timezone.utc).isoformat(),
            "parameters": {
                "repo": before["repo"],
                "split": split.isoformat(),
                "period_lookback_days": days,
                "rework_horizon_days": rework_days,
                "agents_scanned": before["agents_scanned"],
                "horizons_days": list(horizons),
                "headline_horizon_days": headline_horizon,
            },
            "periods": {"before": before, "after": after},
            "comparison": _comparison(before, after),
            "notes": [
                "before = split-lookback..split, after = split..now; every rate is measured within its own period only",
                "cohort labels are evidence grades (certain/probable/human), not authorship verdicts; evidence distributions ship with every table",
                "this is a measurement of one repository in two windows, not a verdict on AI-assisted development",
            ],
        }
    )


def _period(label: str, report: dict) -> dict:
    m11 = report["m11_rework"]
    total_cost = sum(info["cost_usd"] for info in report["m4_accepted"]["totals"].values())
    return {
        "label": label,
        "repo": report["parameters"]["repo"],
        "agents_scanned": report["parameters"]["agents_scanned"],
        "window_start": report["parameters"]["window_start"],
        "window_end": report["parameters"]["window_end"],
        "sessions": report["input"]["sessions"],
        "api_calls": report["input"]["api_calls"],
        "commits": report["input"]["commits_in_window"],
        "attributed_commits": report["input"]["attributed_commits"],
        "session_cost_usd": round(total_cost, 6),
        "cohort_evidence": m11["cohort_evidence"],
        "cohorts": m11["cohorts"],
        "survival_rate": report["m1_survival"]["overall_rate"],
    }


def _comparison(before: dict, after: dict) -> dict:
    """Headline deltas: AI-combined vs human rework, after period focus."""

    def rate(period: dict, cohort: str):
        value = period["cohorts"].get(cohort, {}).get("rework_rate")
        return value

    ai_after, human_after = rate(after, "ai_combined"), rate(after, "human")
    ai_before, human_before = rate(before, "ai_combined"), rate(before, "human")

    def ratio(ai, human):
        if ai is None or not human:
            return None
        return ai / human

    return {
        "measurement": "measured_from_git_history",
        "ai_rework_rate": {"before": ai_before, "after": ai_after},
        "human_rework_rate": {"before": human_before, "after": human_after},
        "ai_vs_human_rework_ratio": {"before": ratio(ai_before, human_before), "after": ratio(ai_after, human_after)},
        "survival_rate": {"before": before["survival_rate"], "after": after["survival_rate"]},
        "note": "rates are per-period; ratio = ai_combined / human rework rate (None when the denominator is unmeasurable)",
    }
