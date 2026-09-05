"""Transcript ingestion: vendor adapters behind one load entry point.

Public API (stable for callers inside and outside the package):

- ``load_sessions(repo, transcripts_root, *, now, days, logger, agents)``
- ``resolve_roots(agents, override)`` — per-vendor transcript roots
- ``ADAPTERS`` / ``DEFAULT_AGENTS`` — the adapter registry

Adding a vendor: subclass :class:`.base.TranscriptAdapter`, register an
instance in ``ADAPTERS``, and add it to ``DEFAULT_AGENTS``. Nothing else in
the pipeline needs to change — every lens consumes the vendor-neutral
:class:`yield_audit.events.Session` model.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from ..events import Session
from .base import (
    TranscriptAdapter,
    count_records,
    group_edit_files_by_repo,
    normalize_path,
    relpath_inside,
    sessions_by_id,
)
from .claude import ClaudeAdapter, iter_transcript_files, munged_project_dir_name
from .codex import CodexAdapter

ADAPTERS: dict[str, TranscriptAdapter] = {
    adapter.name: adapter for adapter in (ClaudeAdapter(), CodexAdapter())
}
DEFAULT_AGENTS: tuple[str, ...] = tuple(sorted(ADAPTERS))


def default_transcripts_root() -> Path:
    """Primary default: the Claude Code projects directory (back-compat)."""
    return ADAPTERS["claude"].default_root()


def resolve_roots(agents, override: Path | None) -> dict[str, Path]:
    """Per-agent transcript roots.

    An explicit ``override`` (``--transcripts-dir``) is applied to every
    agent — adapters skip records that are not their vendor's schema, so
    cross-scanning a shared directory is harmless. Without an override each
    agent uses its own default root, and roots that do not exist are
    skipped (the vendor's CLI is simply not installed).
    """
    roots: dict[str, Path] = {}
    for name in agents:
        adapter = ADAPTERS.get(name)
        if adapter is None:
            raise ValueError(f"unknown agent {name!r}; known: {', '.join(sorted(ADAPTERS))}")
        root = Path(override) if override is not None else adapter.default_root()
        if override is None and not root.is_dir():
            continue
        roots[name] = root
    return roots


def load_sessions(
    repo: str,
    transcripts_root: Path | None,
    *,
    now,
    days: int,
    logger=None,
    agents=None,
) -> list[Session]:
    """Return sessions whose ``cwd`` matches ``repo``, ending within the last ``days``.

    ``agents`` selects which vendors to scan (default: all registered). Session
    ids are namespaced per vendor (``"<vendor>:<raw id>"``) so different vendors
    never collide. Sidechain records (subagent transcripts) are excluded by the
    Claude adapter: their API calls are billed under the parent conversation's
    account but inflate per-session noise without changing commit attribution.
    Set ``days`` <= 0 to disable the time filter.
    """
    repo_real = normalize_path(repo)
    cutoff = None
    if days and days > 0:
        cutoff = now - timedelta(days=days)

    agents = tuple(agents) if agents else DEFAULT_AGENTS
    roots = resolve_roots(agents, transcripts_root)

    sessions: dict[str, Session] = {}
    for name in sorted(roots):
        adapter = ADAPTERS[name]
        jsonl_files = adapter.iter_files(roots[name], repo_real, logger)
        for jsonl in jsonl_files:
            try:
                adapter.ingest_file(jsonl, repo_real, sessions)
            except (OSError, UnicodeDecodeError) as exc:
                if logger:
                    logger(f"skip unreadable transcript {jsonl.name}: {exc.__class__.__name__}")
        if not jsonl_files and logger:
            logger(f"no *.jsonl transcripts found under {roots[name]}")

    selected = []
    for session in sessions.values():
        if session.cwd != repo_real:
            continue
        if cutoff is not None and session.end < cutoff:
            continue
        selected.append(session)
    selected.sort(key=lambda s: s.start)
    return selected


__all__ = [
    "ADAPTERS",
    "DEFAULT_AGENTS",
    "ClaudeAdapter",
    "CodexAdapter",
    "TranscriptAdapter",
    "count_records",
    "default_transcripts_root",
    "group_edit_files_by_repo",
    "iter_transcript_files",
    "load_sessions",
    "munged_project_dir_name",
    "normalize_path",
    "relpath_inside",
    "resolve_roots",
    "sessions_by_id",
]
