"""``yield-audit`` command line interface.

``audit``  — run the pipeline against a git repository and print/write a report.
``aidd``   — AI-transition cohort comparison: two windows split at a transition
             date, AI-vs-human rework rates per period.
``doctor`` — check the environment (git, transcripts, session discovery) without
             computing any metrics.
``export`` — write session timelines to another tool's format (--perfetto).

Exit codes: 0 success, 2 configuration/environment error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, audit, gitdata, transcripts
from . import aidd as aidd_mod
from .report import render_aidd_console, render_aidd_markdown, render_console, render_markdown


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yield-audit",
        description=(
            "Outcome accounting for AI coding agents: what survived, what it cost, "
            "and what was wasted. Fully local; read-only."
        ),
    )
    parser.add_argument("--version", action="version", version=f"yield-audit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="measure outcomes for a git repository")
    p_audit.add_argument("--repo", required=True, help="path to the git repository to audit")
    p_audit.add_argument(
        "--transcripts-dir",
        type=Path,
        default=None,
        help=(
            "transcripts root override, applied to every selected agent "
            f"(default: each agent's own root, e.g. {transcripts.default_transcripts_root()})"
        ),
    )
    p_audit.add_argument(
        "--agent",
        default="auto",
        help=f"which agent's transcripts to scan: auto ({', '.join(transcripts.DEFAULT_AGENTS)}), "
        "or one vendor's name (default auto)",
    )
    p_audit.add_argument(
        "--days",
        type=int,
        default=30,
        help=(
            "window in days for sessions AND commits (default 30, 0 = all); "
            "the M11 probable cohort exists only where an agent session falls inside this window"
        ),
    )
    p_audit.add_argument(
        "--horizon",
        type=int,
        default=7,
        help="headline survival horizon in days (default 7)",
    )
    p_audit.add_argument(
        "--horizons",
        type=str,
        default="7,30",
        help="M1 survival snapshot horizons in days, comma-separated (default '7,30') — independent of --days",
    )
    p_audit.add_argument("--pricing-file", type=Path, default=None, help="JSON price overrides (USD per MTok)")
    p_audit.add_argument(
        "--proximity-hours",
        type=int,
        default=24,
        help="session-commit time window for attribution (default 24)",
    )
    p_audit.add_argument(
        "--rework-days",
        type=int,
        default=14,
        help="rework horizon for the M11 cohort lens in days (default 14, 0 = skip)",
    )
    p_audit.add_argument(
        "--settle-days",
        type=int,
        default=90,
        help="M12 settle-rate horizon in days (default 90, 0 = skip)",
    )
    p_audit.add_argument(
        "--ci-runs",
        type=Path,
        default=None,
        help="gh CI export for the M13 lens: gh run list -R owner/repo --json databaseId,headSha,conclusion > ci.json",
    )
    p_audit.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the persistent blame/tree cache (~/.cache/yield-audit; content-addressed by git SHA, never affects output)",
    )
    p_audit.add_argument(
        "--format",
        choices=("console", "json", "markdown"),
        default="console",
        help="report format (default console)",
    )
    p_audit.add_argument("--out", type=Path, default=None, help="also write the JSON report to this path")
    p_audit.add_argument("--show-paths", action="store_true", help="show full file paths (default: basenames only)")
    p_audit.add_argument("--details", action="store_true", help="include per-file survival units in JSON")
    p_audit.add_argument(
        "--now",
        type=str,
        default=None,
        help="override 'now' as ISO-8601 (for reproducible runs); defaults to the current time",
    )

    p_aidd = sub.add_parser(
        "aidd",
        help="AI-transition cohort comparison: two windows split at a transition date",
    )
    p_aidd.add_argument("--repo", required=True, help="path to the git repository to audit")
    p_aidd.add_argument("--split", type=str, required=True, help="transition date, ISO-8601 (e.g. 2026-03-01)")
    p_aidd.add_argument("--days", type=int, default=90, help="per-period lookback in days (default 90)")
    p_aidd.add_argument("--transcripts-dir", type=Path, default=None, help="transcripts root override (all agents)")
    p_aidd.add_argument("--agent", default="auto", help="which agent's transcripts to scan (default auto)")
    p_aidd.add_argument("--rework-days", type=int, default=14, help="rework horizon in days (default 14)")
    p_aidd.add_argument("--pricing-file", type=Path, default=None, help="JSON price overrides (USD per MTok)")
    p_aidd.add_argument("--proximity-hours", type=int, default=24, help="attribution time window (default 24)")
    p_aidd.add_argument("--no-cache", action="store_true", help="skip the persistent blame/tree cache")
    p_aidd.add_argument(
        "--format",
        choices=("console", "json", "markdown"),
        default="console",
        help="report format (default console)",
    )
    p_aidd.add_argument("--out", type=Path, default=None, help="also write the JSON report to this path")
    p_aidd.add_argument("--now", type=str, default=None, help="override 'now' as ISO-8601 (reproducible runs)")

    p_doctor = sub.add_parser("doctor", help="check environment and session discovery")
    p_doctor.add_argument("--repo", default=None, help="optionally verify sessions exist for this repository")
    p_doctor.add_argument("--transcripts-dir", type=Path, default=None)
    p_doctor.add_argument("--agent", default="auto", help="agent vendors to check (default auto)")

    p_export = sub.add_parser(
        "export",
        help="write session timelines to another tool's format",
    )
    p_export.add_argument(
        "--perfetto",
        action="store_true",
        help="write a Perfetto trace JSON that loads in ui.perfetto.dev "
        "(requires the perfetto extra: pip install 'yield-audit[perfetto]')",
    )
    p_export.add_argument("--repo", required=True, help="path to the git repository the sessions ran in")
    p_export.add_argument("--transcripts-dir", type=Path, default=None, help="transcripts root override")
    p_export.add_argument("--agent", default="auto", help="which agent's transcripts to scan (default auto)")
    p_export.add_argument("--days", type=int, default=30, help="session window in days (default 30, 0 = all)")
    p_export.add_argument("--out", type=Path, required=True, help="output trace path (.perfetto.json)")
    p_export.add_argument("--now", type=str, default=None, help="override 'now' as ISO-8601 (reproducible runs)")

    p_snap = sub.add_parser(
        "snapshot",
        help="pre-warm the persistent blame/tree cache (run periodically, e.g. from cron)",
    )
    p_snap.add_argument("--repo", required=True, help="path to the git repository to snapshot")
    p_snap.add_argument("--transcripts-dir", type=Path, default=None, help="transcripts root override (all agents)")
    p_snap.add_argument("--agent", default="auto", help="which agent's transcripts to scan (default auto)")
    p_snap.add_argument("--days", type=int, default=30, help="window in days (default 30, 0 = all)")
    p_snap.add_argument("--horizon", type=int, default=7, help="headline survival horizon in days (default 7)")
    p_snap.add_argument("--horizons", type=str, default="7,30", help="comma-separated survival horizons (default '7,30')")
    p_snap.add_argument("--rework-days", type=int, default=14, help="M11 rework horizon in days (default 14)")
    p_snap.add_argument("--settle-days", type=int, default=90, help="M12 settle horizon in days (default 90)")
    p_snap.add_argument("--proximity-hours", type=int, default=24, help="attribution time window (default 24)")
    p_snap.add_argument("--now", type=str, default=None, help="override 'now' as ISO-8601 (reproducible runs)")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:  # report text is user-data derived; never crash on a hostile locale
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - non-text streams
        pass
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "aidd":
            return _run_aidd(args)
        if args.command == "snapshot":
            return _run_snapshot(args)
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "export":
            return _run_export(args)
    except (audit.AuditError, gitdata.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover - parser.error exits


def _resolve_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    from .events import parse_iso8601

    parsed = parse_iso8601(raw)
    if parsed is None:
        raise audit.AuditError(f"--now is not a valid ISO-8601 timestamp: {raw!r}")
    return parsed


def _resolve_agents(raw: str) -> tuple[str, ...]:
    if raw == "auto":
        return transcripts.DEFAULT_AGENTS
    if raw in transcripts.ADAPTERS:
        return (raw,)
    raise audit.AuditError(
        f"unknown agent {raw!r}; known: auto, {', '.join(transcripts.DEFAULT_AGENTS)}"
    )


def _stderr_logger(message: str):
    # progress goes to stderr so report stdout stays machine-parseable
    print(message, file=sys.stderr)


def _run_audit(args) -> int:
    if args.days < 0:
        raise audit.AuditError("--days must be >= 0 (0 disables the time window)")
    if args.rework_days < 0:
        raise audit.AuditError("--rework-days must be >= 0 (0 skips the M11 rework lens)")
    if args.settle_days < 0:
        raise audit.AuditError("--settle-days must be >= 0 (0 skips the M12 settle lens)")
    repo = Path(args.repo).expanduser().resolve()
    transcripts_root = args.transcripts_dir  # None = each agent's own root
    horizons = _parse_horizons(args.horizons)
    now = _resolve_now(args.now)
    agents = _resolve_agents(args.agent)

    report = audit.run_audit(
        repo=str(repo),
        transcripts_root=transcripts_root,
        days=args.days,
        horizons=horizons,
        headline_horizon=args.horizon,
        now=now,
        pricing_override=str(args.pricing_file) if args.pricing_file else None,
        proximity_hours=args.proximity_hours,
        show_paths=args.show_paths,
        details=args.details,
        agents=agents,
        rework_days=args.rework_days,
        settle_days=args.settle_days,
        use_cache=not args.no_cache,
        ci_runs_path=str(args.ci_runs) if args.ci_runs else None,
        log=_stderr_logger,
    )
    _emit(report, args)
    return 0


def _run_aidd(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    now = _resolve_now(args.now)
    from .events import parse_iso8601

    split = parse_iso8601(args.split)
    if split is None:
        raise audit.AuditError(f"--split is not a valid ISO-8601 date: {args.split!r}")
    if args.days <= 0:
        raise audit.AuditError("--days must be > 0 (per-period lookback)")
    if args.rework_days <= 0:
        raise audit.AuditError("--rework-days must be > 0 for aidd")

    report = aidd_mod.run_aidd(
        repo=str(repo),
        transcripts_root=args.transcripts_dir,
        split=split,
        days=args.days,
        now=now,
        horizons=(7, 30),
        headline_horizon=7,
        pricing_override=str(args.pricing_file) if args.pricing_file else None,
        proximity_hours=args.proximity_hours,
        rework_days=args.rework_days,
        agents=_resolve_agents(args.agent),
        use_cache=not args.no_cache,
        log=_stderr_logger,
    )
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_aidd_markdown(report))
    else:
        print(render_aidd_console(report))
    return 0


def _emit(report: dict, args) -> None:
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_markdown(report))
    else:
        print(render_console(report))


def _run_snapshot(args) -> int:
    """Run the full pipeline once to warm the persistent cache, quietly.

    Output is deterministic counts only — no wall clock, no metrics — so a
    cron job's log stays diffable. Repeat audits with the same window
    parameters are then served from the cache instead of re-blaming.
    """
    from . import cache

    if args.days < 0:
        raise audit.AuditError("--days must be >= 0 (0 disables the time window)")
    repo = str(Path(args.repo).expanduser().resolve())
    report = audit.run_audit(
        repo=repo,
        transcripts_root=args.transcripts_dir,
        days=args.days,
        horizons=_parse_horizons(args.horizons),
        headline_horizon=args.horizon,
        now=_resolve_now(args.now),
        pricing_override=None,
        proximity_hours=args.proximity_hours,
        show_paths=False,
        details=False,
        agents=_resolve_agents(args.agent),
        rework_days=args.rework_days,
        settle_days=args.settle_days,
        use_cache=True,
        log=None,
    )
    entries = len(cache.load(repo))
    print(f"snapshot ok: {report['input']['sessions']} sessions, {report['input']['commits_in_window']} commits scanned")
    print(f"cache: {entries} immutable git-fact entries at {cache.cache_path(repo)}")
    print("repeat audits with these window parameters skip the cached blame/tree work")
    return 0


def _run_export(args) -> int:
    import json

    from . import export

    if not args.perfetto:
        raise audit.AuditError("choose an export format: --perfetto")
    if args.days < 0:
        raise audit.AuditError("--days must be >= 0 (0 disables the time window)")
    repo = str(Path(args.repo).expanduser().resolve())
    sessions = transcripts.load_sessions(
        repo,
        args.transcripts_dir,
        now=_resolve_now(args.now),
        days=args.days,
        agents=_resolve_agents(args.agent),
        logger=_stderr_logger,
    )
    trace = export.sessions_to_perfetto(sessions)
    args.out.write_text(json.dumps(trace, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"exported {len(sessions)} session(s) -> "
        f"{len(trace['traceEvents'])} trace event(s) -> {args.out}"
    )
    print("open https://ui.perfetto.dev and drag the file in (parsed locally, never uploaded)")
    return 0


def _parse_horizons(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError as exc:
            raise audit.AuditError(f"--horizons must be integers, got {piece!r}") from exc
        if value <= 0:
            raise audit.AuditError("--horizons values must be positive")
        values.append(value)
    if not values:
        raise audit.AuditError("--horizons must contain at least one value")
    return tuple(sorted(set(values)))


def _run_doctor(args) -> int:
    checks: list[tuple[bool, str]] = []
    import shutil

    git_ok = shutil.which("git") is not None
    checks.append((git_ok, f"git on PATH: {git_ok}"))

    agents = _resolve_agents(args.agent)
    roots = transcripts.resolve_roots(agents, args.transcripts_dir)
    for name in sorted(agents):
        root = roots.get(name)
        if root is None:
            checks.append((False, f"agent {name}: transcripts root not found ({transcripts.ADAPTERS[name].default_root()})"))
            continue
        jsonls = list(root.rglob("*.jsonl"))
        size_mb = sum(f.stat().st_size for f in jsonls if f.is_file()) / 1_000_000
        checks.append(
            (bool(jsonls), f"agent {name}: transcripts root {root} ({len(jsonls)} jsonl files, {size_mb:.1f} MB)")
        )

    total_sessions = 0
    if args.repo:
        repo = str(Path(args.repo).expanduser().resolve())
        for name in sorted(agents):
            agent_sessions = transcripts.load_sessions(
                repo, args.transcripts_dir, now=datetime.now(timezone.utc), days=0, agents=(name,)
            )
            checks.append((True, f"agent {name}: sessions with cwd == repo: {len(agent_sessions)}"))
            total_sessions += len(agent_sessions)
        checks.append((total_sessions > 0, f"sessions with cwd == repo (all agents): {total_sessions}"))
        if not gitdata.is_git_repo(repo):
            checks.append((False, f"not a git repository: {repo}"))

    for ok, message in checks:
        print(("ok  " if ok else "FAIL") + "  " + message)
    return 0 if all(ok for ok, _ in checks) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
