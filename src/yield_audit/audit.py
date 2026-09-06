"""Audit pipeline: normalize inputs, run lenses, assemble the report dict.

Every metric block carries a ``measurement`` label so no reader can mistake
an estimate for an observation:

- ``observed``  — read straight from local records (token counts, commands).
- ``estimate``  — observed values × list-price assumptions (USD figures).
- ``proxy``     — a stated substitute stands in for an unobservable quantity
                  (e.g. line-share standing in for per-commit token share).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cache, cohorts, gitdata, redact, transcripts
from .attribute import attribute as attribute_fn
from .costs import session_cost
from .lenses.accepted import analyze_accepted
from .lenses.cache_locality import analyze_cache_locality
from .lenses.incident import analyze_incidents
from .lenses.retry import analyze_retry
from .lenses.rework import analyze_rework
from .lenses.settle import analyze_settle
from .lenses.survival import analyze_survival
from .lenses.verify_gap import analyze_verify_gap
from .lenses.verify_transfer import analyze_verify_transfer, parse_ci_runs
from .lenses.waste import analyze_waste
from .pricing import load_pricing

SCHEMA_VERSION = "yieldaudit.report.v1"


class AuditError(RuntimeError):
    """User-facing configuration/environment problem."""


def run_audit(
    *,
    repo: str,
    transcripts_root: Path | None,
    days: int,
    horizons: tuple[int, ...],
    headline_horizon: int,
    now: datetime,
    pricing_override: str | None,
    proximity_hours: int,
    show_paths: bool,
    details: bool,
    agents=None,
    rework_days: int = 14,
    settle_days: int = 90,
    since: datetime | None = None,
    until: datetime | None = None,
    use_cache: bool = True,
    ci_runs_path: str | None = None,
    log=None,
) -> dict:
    if not gitdata.is_git_repo(repo):
        raise AuditError(f"not a git repository: {repo}")
    if headline_horizon not in horizons:
        horizons = (headline_horizon, *horizons)

    repo_real = transcripts.normalize_path(repo)
    log_messages: list[str] = []
    if transcripts_root is not None and not Path(transcripts_root).is_dir():
        raise AuditError(f"transcripts dir not found: {transcripts_root}")
    roots = transcripts.resolve_roots(agents or transcripts.DEFAULT_AGENTS, transcripts_root)
    if not roots:
        tried = ", ".join(
            f"{name} {transcripts.ADAPTERS[name].default_root()}"
            for name in sorted(agents or transcripts.DEFAULT_AGENTS)
        )
        raise AuditError(
            f"no agent transcripts found (tried: {tried}) — "
            "pass --transcripts-dir or run `yield-audit doctor`"
        )
    for name in sorted(set(transcripts.DEFAULT_AGENTS) - set(roots)):
        log_messages.append(f"agent {name}: transcripts root not found, skipped")
    if log is not None:
        sessions = transcripts.load_sessions(
            repo_real, transcripts_root, now=now, days=days, agents=agents, since=since, until=until,
            logger=lambda m: (log_messages.append(m), log(m))[1],
        )
    else:
        sessions = transcripts.load_sessions(
            repo_real, transcripts_root, now=now, days=days, agents=agents, since=since, until=until,
            logger=log_messages.append,
        )
    if not sessions:
        log_messages.append(
            "0 agent sessions matched this repo in the window — "
            "run `yield-audit doctor --repo <repo>` to check transcript discovery"
        )
    transcripts.group_edit_files_by_repo(sessions, repo_real)

    # An explicit `since` (the aidd period split) overrides the days window.
    window_since = since if since is not None else (now - timedelta(days=days) if days and days > 0 else None)
    git_warnings: list[str] = []
    commits = gitdata.commits_with_numstat(repo_real, since=window_since, until=until, warnings=git_warnings)
    commits_by_sha = {c.sha: c for c in commits}

    attributions = attribute_fn(
        sessions,
        commits,
        proximity=timedelta(hours=proximity_hours),
    )

    messages = gitdata.commit_messages(repo_real, since=window_since, until=until)
    cohort_labels = cohorts.label_commits(messages, attributions.claimed_shas)

    table, fallback, pricing_notes = load_pricing(pricing_override)

    session_costs = {}
    cost_objects = {}
    for session in sessions:
        cost = session_cost(session, table, fallback)
        cost_objects[session.session_id] = cost
        session_costs[session.session_id] = {
            "cost_usd": cost.cost_usd,
            "total_tokens": cost.total_input_tokens + cost.output_tokens,
        }

    # One shared cache across lenses: blame/snapshot results plus the
    # single touch-map pass that lets both skip blaming files no commit
    # touched inside the measurement window. Seeded from the persistent
    # content-addressed store when enabled (immutable git facts only).
    blame_cache: dict = cache.load(repo_real) if use_cache else {}
    survival = analyze_survival(
        repo_real,
        attributions,
        commits_by_sha,
        now=now,
        horizons=horizons,
        headline_horizon=headline_horizon,
        blame_cache=blame_cache,
        touch_since=window_since,
    )
    waste = analyze_waste(survival, {sid: c["cost_usd"] for sid, c in session_costs.items()}, headline_horizon)

    commit_dates: dict[str, datetime] = {}
    committed_ids: set[str] = set()
    for pair in attributions.pairs:
        committed_ids.add(pair.session_id)
        commit = commits_by_sha.get(pair.commit_sha)
        if commit is not None:
            prev = commit_dates.get(pair.session_id)
            commit_dates[pair.session_id] = max(prev, commit.date) if prev else commit.date

    survival_rates = {
        sid: info["rate"] if info["added"] > 0 else None
        for sid, info in survival.sessions.items()
    }
    verify = analyze_verify_gap(sessions, commit_dates, survival_rates)
    accepted = analyze_accepted(session_costs, survival.sessions, committed_ids)

    retry_by_session = {s.session_id: analyze_retry(s) for s in sessions}
    cache_by_session = {
        s.session_id: analyze_cache_locality(s, table, fallback) for s in sessions
    }

    rework = analyze_rework(
        repo_real,
        commits,
        cohort_labels,
        now=now,
        horizon_days=rework_days,
        blame_cache=blame_cache,
        touch_since=window_since,
    )
    settle = analyze_settle(
        repo_real,
        commits,
        cohort_labels,
        now=now,
        horizon_days=settle_days,
        blame_cache=blame_cache,
        touch_since=window_since,
    )
    incidents = analyze_incidents(repo_real, commits, cohort_labels, blame_cache=blame_cache)

    ci_runs = None
    if ci_runs_path is not None:
        import json as _json

        try:
            with open(ci_runs_path, encoding="utf-8") as handle:
                ci_runs = parse_ci_runs(_json.load(handle))
        except (OSError, ValueError) as exc:
            raise AuditError(f"--ci-runs could not be read as a gh JSON export: {exc}") from exc
    verify_transfer = analyze_verify_transfer(commits, cohort_labels, ci_runs)

    unknown_models: set[str] = set()
    for cost in cost_objects.values():
        unknown_models |= cost.unknown_models

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "parameters": {
            "repo": repo_real if show_paths else redact.abbreviate_home(repo_real),
            "window_days": days,
            "horizons_days": list(horizons),
            "headline_horizon_days": headline_horizon,
            "agents_scanned": sorted(roots),
            "transcripts_root": redact.abbreviate_home(str(roots[sorted(roots)[0]])) if roots else "",
            "transcripts_roots": {
                name: redact.abbreviate_home(str(root)) for name, root in sorted(roots.items())
            },
            "attribution_proximity_hours": proximity_hours,
            "rework_horizon_days": rework_days,
            "settle_horizon_days": settle_days,
            "window_start": window_since.isoformat() if window_since else None,
            "window_end": until.isoformat() if until else None,
            "cache": "on" if use_cache else "off",
            "pricing_source": "builtin_2026-09" if not pricing_override else str(pricing_override),
        },
        "input": {
            "sessions": len(sessions),
            "api_calls": sum(len(s.api_calls) for s in sessions),
            "commits_in_window": len(commits),
            "attributed_commits": len(attributions.claimed_shas),
            "unclaimed_commits": len(attributions.unclaimed_commits),
            "ambiguous_commits": attributions.ambiguous_commits,
            "unknown_models": sorted(redact.sanitize_text(m) for m in unknown_models),
        },
        "attribution": _attribution_block(attributions),
        "m1_survival": _survival_block(survival, show_paths, details),
        "m2_waste": _waste_block(waste),
        "m3_retry": _retry_block(retry_by_session, show_paths),
        "m4_accepted": _accepted_block(accepted),
        "m5_cache": _cache_block(cache_by_session),
        "m8_verify": _verify_block(verify),
        "m11_rework": _rework_block(rework, details),
        "m12_settle": _settle_block(settle, details),
        "m14_incident": _incident_block(incidents),
        "m13_verify_transfer": _verify_transfer_block(verify_transfer),
        "notes": _global_notes(pricing_notes) + git_warnings + log_messages[:20],
    }
    if use_cache:
        cache.save(repo_real, blame_cache)
    # Defense in depth: nothing in the report bypasses the output boundary,
    # even if a future field forgets to sanitize.
    return redact.deep_sanitize(report)


def _attribution_block(attributions) -> dict:
    grades: dict[str, int] = {}
    for pair in attributions.pairs:
        grades[pair.grade] = grades.get(pair.grade, 0) + 1
    return {
        "measurement": "heuristic_matching_with_confidence_grades",
        "pairs": len(attributions.pairs),
        "grades": grades,
        "ambiguous_commits": attributions.ambiguous_commits,
        "note": "high = session ran the commit itself; medium = shared edited files within the time window; contested commits are split and flagged",
    }


def _survival_block(survival, show_paths: bool, details: bool) -> dict:
    block = {
        "measurement": "measured_from_git_history",
        "horizon_days": survival.horizon,
        "overall_rate": _round(survival.overall),
        "added_lines": _num(survival.overall_added),
        "survived_lines": _num(survival.overall_survived),
        "pending_units": survival.pending_count,
        "by_kind": {
            kind: {
                "added": _num(info["added"]),
                "survived": _num(info["survived"]),
                "rate": _round(info["rate"]),
            }
            for kind, info in survival.by_kind.items()
        },
        "per_session": {
            _sid(sid): {
                "added": _num(info["added"]),
                "survived": _num(info["survived"]),
                "rate": _round(info["rate"]),
                "pending": info["pending"],
            }
            for sid, info in sorted(survival.sessions.items())
        },
        "notes": survival.notes,
    }
    if details:
        block["units"] = [
            {
                "session": _sid(u.session_id),
                "path": redact.redact_path(u.path, show_paths=show_paths),
                "kind": u.kind,
                "added": u.added,
                "survived": u.survived.get(survival.horizon),
                "deleted": u.deleted.get(survival.horizon),
                "pending": survival.horizon in u.pending_horizons,
            }
            for u in survival.units
        ]
    return block


def _waste_block(waste) -> dict:
    total_lower = sum(b.lower_usd for b in waste.values())
    total_upper = sum(b.upper_usd for b in waste.values())
    block = {
        "measurement": "estimate_with_bounds",
        "total_lower_usd": round(total_lower, 6),
        "total_upper_usd": round(total_upper, 6),
        "method": "session cost x attribution-share-weighted line-share proxy x waste class (removed=lower+upper, rewritten>=50% lost=upper only)",
        "per_session": {
            _sid(sid): {
                "lower_usd": round(b.lower_usd, 6),
                "upper_usd": round(b.upper_usd, 6),
                "removed_lines": _num(b.removed_lines),
                "rewritten_lines": _num(b.rewritten_lines),
                "edited_lines": _num(b.edited_lines),
            }
            for sid, b in sorted(waste.items())
        },
    }
    return block


def _retry_block(retry_by_session, show_paths: bool) -> dict:
    total_tax_tokens = sum(r.tax_tokens for r in retry_by_session.values())
    total_tokens = sum(r.total_tokens for r in retry_by_session.values())
    chains = [
        {
            "session": _sid(sid),
            "command": redact.sanitize_command(c.command, show_paths=show_paths, limit=80),
            "attempts": c.attempts,
            "errors": c.errors,
        }
        for sid, r in sorted(retry_by_session.items())
        for c in r.chains
    ]
    return {
        "measurement": "observed_from_transcripts",
        "total_tax_tokens": total_tax_tokens,
        "total_tokens": total_tokens,
        "tax_share": _round(total_tax_tokens / total_tokens) if total_tokens else None,
        "failure_chains": chains[:200],
        "chains_truncated": max(0, len(chains) - 200),
        "per_session": {
            _sid(sid): {
                "tax_tokens": r.tax_tokens,
                "tax_share": _round(r.tax_token_share),
                "chains": len(r.chains),
            }
            for sid, r in sorted(retry_by_session.items())
            if r.chains
        },
    }


def _accepted_block(accepted) -> dict:
    return {
        "measurement": "estimate_observed_tokens_x_list_price",
        "cost_per_accepted_usd": _round(accepted.cost_per_accepted_usd, 6),
        "tokens_per_accepted": accepted.tokens_per_accepted,
        "accept_threshold_survival": 0.5,
        "totals": {
            status: {
                "sessions": info["sessions"],
                "cost_usd": _round(info["cost_usd"], 6),
                "total_tokens": info["total_tokens"],
            }
            for status, info in accepted.totals.items()
        },
        "per_session": {
            _sid(sid): {
                "status": info["status"],
                "cost_usd": _round(info["cost_usd"], 6),
                "survival_rate": _round(info["survival_rate"]),
            }
            for sid, info in sorted(accepted.sessions.items())
        },
    }


def _cache_block(cache_by_session) -> dict:
    total_wasted = sum(r.wasted_usd for r in cache_by_session.values())
    cold_total = sum(r.cold_calls for r in cache_by_session.values())
    by_class: dict[str, int] = {}
    for r in cache_by_session.values():
        for cls, count in r.by_class.items():
            by_class[cls] = by_class.get(cls, 0) + count
    rates = [r.hit_rate for r in cache_by_session.values() if r.hit_rate is not None]
    events = []
    for sid, r in sorted(cache_by_session.items()):
        for e in r.events:
            if e.class_name == "compaction":
                continue
            events.append(
                {
                    "session": _sid(sid),
                    "ts": e.ts.isoformat(),
                    "class": e.class_name,
                    "gap_seconds": round(e.gap_seconds) if e.gap_seconds is not None else None,
                    "input_tokens": e.input_tokens,
                    "wasted_usd": _round(e.wasted_usd, 6),
                }
            )
    return {
        "measurement": "estimate_observed_tokens_x_list_price",
        "cold_calls": cold_total,
        "cold_by_class": by_class,
        "wasted_usd": _round(total_wasted, 6),
        "mean_session_hit_rate": _round(sum(rates) / len(rates)) if rates else None,
        "events": events[:200],
        "events_truncated": max(0, len(events) - 200),
        "notes": [
            "wasted_usd = what non-compaction cold input would have cost at cache-read price; compaction rebuilds are excluded by design"
        ],
    }


def _rework_block(rework, details: bool) -> dict:
    block = {
        "measurement": "measured_from_git_history",
        "rework_horizon_days": rework.horizon_days,
        "cohort_evidence": dict(sorted(rework.evidence.items())),
        "cohorts": {
            label: {
                "commits": info["commits"],
                "measured_commits": info["measured_commits"],
                "pending_commits": info["pending_commits"],
                "added_lines": _num(info["added"]),
                "reworked_lines": _num(info["reworked"]),
                "rework_rate": _round(info["rate"]),
            }
            for label, info in sorted(rework.cohorts.items())
        },
        "notes": rework.notes,
    }
    if details:
        block["commits"] = rework.commits[:200]
        block["commits_truncated"] = max(0, len(rework.commits) - 200)
    return block


def _settle_block(settle, details: bool) -> dict:
    block = {
        "measurement": "measured_from_git_history",
        "settle_horizon_days": settle.horizon_days,
        "cohort_evidence": dict(sorted(settle.evidence.items())),
        "cohorts": {
            label: {
                "commits": info["commits"],
                "measured_commits": info["measured_commits"],
                "pending_commits": info["pending_commits"],
                "added_lines": _num(info["added"]),
                "survived_lines": _num(info["survived"]),
                "settle_rate": _round(info["rate"]),
            }
            for label, info in sorted(settle.cohorts.items())
        },
        "notes": settle.notes,
    }
    if details:
        block["commits"] = settle.commits[:200]
        block["commits_truncated"] = max(0, len(settle.commits) - 200)
    return block


def _verify_transfer_block(result) -> dict:
    block = {
        "measurement": "observed_from_provided_ci_export",
        "enabled": result.enabled,
        "runs_total": result.runs_total,
        "runs_joined": result.runs_joined,
        "runs_ignored": result.runs_ignored,
        "by_cohort": {label: info for label, info in sorted(result.by_cohort.items())},
        "notes": result.notes,
    }
    return block


def _incident_block(incidents) -> dict:
    return {
        "measurement": "proxy",
        "fix_commits": incidents.fix_commits,
        "targeted_lines_total": incidents.targeted_lines_total,
        "targeted_lines_by_cohort": dict(sorted(incidents.by_cohort.items())),
        "top_origins": incidents.origins,
        "notes": incidents.notes,
    }


def _verify_block(verify) -> dict:
    return {
        "measurement": "observed_from_transcripts",
        "gap_rate": _round(verify.gap_rate),
        "gap_rate_strict": _round(verify.gap_rate_strict),
        "per_session": {
            _sid(sid): {
                "status": info.status,
                "verify_commands": info.verify_count,
            }
            for sid, info in sorted(verify.sessions.items())
        },
        "correlation_with_survival": {
            status: {
                "sessions": info["sessions"],
                "mean_survival": _round(info["mean_survival"]),
            }
            for status, info in verify.correlation.items()
        },
        "notes": verify.notes,
    }


def _global_notes(pricing_notes: list[str]) -> list[str]:
    return [
        "all data stays local: transcripts and git history are read, nothing is uploaded",
        "USD figures multiply observed token counts by list prices; your actual rates may differ (override with --pricing-file)",
        "attribution is heuristic; every dependent metric inherits its confidence grades",
    ] + pricing_notes


def _round(value, digits: int = 6):
    return round(value, digits) if isinstance(value, float) else value


def _num(value):
    """Collapse integral floats (share-weighted sums) to int for display."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _sid(session_id: str) -> str:
    # Session ids are transcript-controlled; they end up as dict keys and
    # table cells, so they pass the same sanitization as everything else.
    # Vendor namespaced ids ("claude:abcd…") keep their prefix so reports
    # stay unambiguous in multi-agent audits.
    safe = redact.sanitize_text(session_id)
    vendor, sep, rest = safe.partition(":")
    return f"{vendor}:{rest[:8]}" if sep else safe[:8]
