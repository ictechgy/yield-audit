"""``yield-audit`` command line interface.

``audit``  — run the pipeline against a git repository and print/write a report.
``doctor`` — check the environment (git, transcripts, session discovery) without
             computing any metrics.

Exit codes: 0 success, 2 configuration/environment error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, audit, gitdata, transcripts
from .report import render_console, render_markdown


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
    p_audit.add_argument("--days", type=int, default=30, help="session/commit window in days (default 30, 0 = all)")
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
        help="comma-separated horizons to measure (default '7,30')",
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

    p_doctor = sub.add_parser("doctor", help="check environment and session discovery")
    p_doctor.add_argument("--repo", default=None, help="optionally verify sessions exist for this repository")
    p_doctor.add_argument("--transcripts-dir", type=Path, default=None)
    p_doctor.add_argument("--agent", default="auto", help="agent vendors to check (default auto)")
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
        if args.command == "doctor":
            return _run_doctor(args)
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


def _run_audit(args) -> int:
    if args.days < 0:
        raise audit.AuditError("--days must be >= 0 (0 disables the time window)")
    if args.rework_days < 0:
        raise audit.AuditError("--rework-days must be >= 0 (0 skips the M11 rework lens)")
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
    )

    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_markdown(report))
    else:
        print(render_console(report))
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
        checks.append((bool(jsonls), f"agent {name}: transcripts root {root} ({len(jsonls)} jsonl files)"))

    total_sessions = 0
    if args.repo:
        repo = str(Path(args.repo).expanduser().resolve())
        sessions = transcripts.load_sessions(
            repo, args.transcripts_dir, now=datetime.now(timezone.utc), days=0, agents=agents
        )
        total_sessions = len(sessions)
        checks.append((total_sessions > 0, f"sessions with cwd == repo: {total_sessions}"))
        if not gitdata.is_git_repo(repo):
            checks.append((False, f"not a git repository: {repo}"))

    for ok, message in checks:
        print(("ok  " if ok else "FAIL") + "  " + message)
    return 0 if all(ok for ok, _ in checks) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
