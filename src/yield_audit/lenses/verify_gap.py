"""M8 — verification gap and its correlation with survival.

A session "verifies" when it runs a command matching a build/test/lint
pattern. For sessions with attributed commits we ask whether verification
happened *before the last attributed commit* — committing without any
mechanical check is the gap. The lens then correlates gap status with the
survival rate so the report can surface findings, not just counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

VERIFY_PATTERN = re.compile(
    r"(?:^|[\s;&|(=/])(?:"
    r"pytest|py\.?test|unittest\s+discover|tox|nox|"
    r"npm(?:\s+\S+)*\s+(?:run\s+)?test|pnpm(?:\s+\S+)*\s+(?:run\s+)?test|yarn(?:\s+\S+)*\s+test|"
    r"vitest|jest|playwright\s+test|cypress\s+run|"
    r"cargo\s+(?:test|build|check|clippy)|"
    r"go\s+(?:test|build|vet)|"
    r"gradlew?(?:\s+\S+)*\s+(?:test|build|check)|mvn(\s+\S+)*\s+(?:test|verify)|"
    r"xcodebuild|swift\s+(?:build|test)|"
    r"make(?:\s|$)|cmake\s+--build|"
    r"ruff(?:\s|$)|eslint(?:\s|$)|mypy(?:\s|$)|tsc(?:\s|$)|golangci-lint"
    r")",
    re.IGNORECASE,
)


@dataclass
class VerificationInfo:
    session_id: str
    status: str  # "verified_before_commit" | "verified_after_commit" | "gap" | "no_commits"
    last_commit_ts: datetime | None = None
    verify_count: int = 0


@dataclass
class VerifyGapResult:
    sessions: dict[str, VerificationInfo] = field(default_factory=dict)
    gap_rate: float | None = None  # never verified, among sessions with commits
    gap_rate_strict: float | None = None  # not verified before the last commit (gap + verified-after)
    correlation: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def analyze_verify_gap(
    sessions,
    commit_dates: dict[str, datetime],  # session_id -> last attributed commit ts
    survival_rates: dict[str, float | None],
    pattern=VERIFY_PATTERN,
) -> VerifyGapResult:
    result = VerifyGapResult()
    for session in sessions:
        stamps = session.verification_commands(pattern)
        last_commit_ts = commit_dates.get(session.session_id)
        if last_commit_ts is None:
            status = "no_commits"
        elif stamps and stamps[0] <= last_commit_ts:
            status = "verified_before_commit"
        elif stamps:
            status = "verified_after_commit"
        else:
            status = "gap"
        result.sessions[session.session_id] = VerificationInfo(
            session_id=session.session_id,
            status=status,
            last_commit_ts=last_commit_ts,
            verify_count=len(stamps),
        )

    committed = [
        info for info in result.sessions.values() if info.status != "no_commits"
    ]
    if committed:
        gaps = [info for info in committed if info.status == "gap"]
        strict = [info for info in committed if info.status in ("gap", "verified_after_commit")]
        result.gap_rate = len(gaps) / len(committed)
        result.gap_rate_strict = len(strict) / len(committed)
    result.notes.append(
        "gap_rate = never verified; gap_rate_strict = not verified before the last attributed commit (includes verified-after)"
    )

    # Correlation: mean survival by verification status (measurable sessions only).
    buckets: dict[str, list[float]] = {}
    for info in committed:
        rate = survival_rates.get(info.session_id)
        if rate is None:
            continue
        buckets.setdefault(info.status, []).append(rate)
    for status, rates in sorted(buckets.items()):
        result.correlation[status] = {
            "sessions": len(rates),
            "mean_survival": sum(rates) / len(rates),
        }
    result.notes.append(
        "correlation is observational; small session counts are not evidence of causation"
    )
    return result
