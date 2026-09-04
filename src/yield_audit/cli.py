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

from . import __version__, audit, transcripts
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
        help=f"Claude Code transcripts root (default: {transcripts.default_transcripts_root()})",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "doctor":
            return _run_doctor(args)
    except audit.AuditError as exc:
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


def _run_audit(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    transcripts_root = args.transcripts_dir or transcripts.default_transcripts_root()
    horizons = _parse_horizons(args.horizons)
    now = _resolve_now(args.now)

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

    transcripts_root = args.transcripts_dir or transcripts.default_transcripts_root()
    checks.append((transcripts_root.is_dir(), f"transcripts root exists: {transcripts_root}"))

    total_sessions = 0
    if transcripts_root.is_dir():
        jsonls = list(transcripts_root.rglob("*.jsonl"))
        checks.append((bool(jsonls), f"transcript files found: {len(jsonls)}"))
        if args.repo:
            repo = str(Path(args.repo).expanduser().resolve())
            sessions = transcripts.load_sessions(
                repo, transcripts_root, now=datetime.now(timezone.utc), days=0
            )
            total_sessions = len(sessions)
            checks.append((total_sessions > 0, f"sessions with cwd == repo: {total_sessions}"))
            if not audit.gitdata.is_git_repo(repo):
                checks.append((False, f"not a git repository: {repo}"))

    for ok, message in checks:
        print(("ok  " if ok else "FAIL") + "  " + message)
    return 0 if all(ok for ok, _ in checks) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
