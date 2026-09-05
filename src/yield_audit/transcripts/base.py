"""Vendor-neutral transcript adapter base.

Each AI coding agent stores its session logs under a different root in a
different JSONL schema. An adapter knows how to (1) find that vendor's
transcript files and (2) turn one JSON record into the vendor-neutral
:class:`yield_audit.events.Session` model every lens consumes.

Session identity is namespaced — ``"<vendor>:<raw id>"`` — so transcripts
from different vendors never collide in the same audit, even when a raw
session id happens to match.

Adapter contract:

- ``name``           — vendor key used in ``--agent`` and session namespaces.
- ``default_root()`` — where this vendor stores transcripts locally.
- ``iter_files``     — files to scan (base implementation: recursive
                       ``*.jsonl`` walk with symlink-cycle protection).
- ``handle_record``  — parse one decoded JSON record into session state,
                       using the per-file ``ctx`` dict for cross-record
                       context (cwd, model, ...).

All parsing must be key-based and defensive: unknown record types, missing
fields, and malformed values are skipped, never trusted.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path

from ..events import Session, parse_iso8601

COMMIT_COMMAND_RE = None  # compiled lazily to keep import time near zero


def _commit_command_re():
    global COMMIT_COMMAND_RE
    if COMMIT_COMMAND_RE is None:
        # "git commit" as an actual argument, not e.g. "git commitlint".
        COMMIT_COMMAND_RE = re.compile(r"(?:^|[\s;&|(])git(?:\s+-[^\s]+\s+)*\s*commit(?:\s|$)")
    return COMMIT_COMMAND_RE


def normalize_path(path: str) -> str:
    try:
        return os.path.realpath(str(path))
    except OSError:
        return str(path)


def _int(value) -> int:
    """Token counts: ints pass; floats (some client versions) truncate; junk is 0.

    ``math.isfinite`` matters: Python's json parser accepts bare ``Infinity``,
    and ``int(inf)`` would raise OverflowError past every guard.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return int(value)
    return 0


def walk_jsonl(root: Path) -> list[Path]:
    out: list[Path] = []
    seen: set[tuple[int, int]] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            stat = os.stat(dirpath)
            identity = (stat.st_dev, stat.st_ino)
        except OSError:
            identity = None
        if identity is not None and identity in seen:
            dirnames[:] = []  # symlink cycle: do not descend again
            continue
        if identity is not None:
            seen.add(identity)
        for filename in filenames:
            if filename.endswith(".jsonl"):
                out.append(Path(dirpath) / filename)
    return sorted(out)


class TranscriptAdapter:
    """Base class: shared JSONL reading and session bookkeeping."""

    name: str = "adapter"

    def default_root(self) -> Path:
        raise NotImplementedError

    def iter_files(self, root: Path, repo_real: str, logger=None) -> list[Path]:
        files = walk_jsonl(root)
        if logger:
            logger(f"[{self.name}] scanning {root} ({len(files)} files)")
        return files

    def ingest_file(self, jsonl_path: Path, repo_real: str, sessions: dict[str, Session]) -> None:
        ctx: dict = {}
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    self.handle_record(record, ctx, jsonl_path, repo_real, sessions)

    def handle_record(
        self, record: dict, ctx: dict, path: Path, repo_real: str, sessions: dict[str, Session]
    ) -> None:
        raise NotImplementedError

    # --- shared session bookkeeping -------------------------------------

    def session_key(self, raw_session_id: str) -> str:
        return f"{self.name}:{raw_session_id}"

    def ensure_session(
        self,
        sessions: dict[str, Session],
        raw_session_id: str,
        cwd: str | None,
        repo_real: str,
        ts,
        path: Path,
    ) -> Session | None:
        """Get or create the namespaced session, updating its time bounds.

        Returns ``None`` when the record's cwd does not resolve to the
        audited repository — those sessions are never created.
        """
        if not isinstance(cwd, str) or not cwd:
            return None
        if normalize_path(cwd) != repo_real:
            return None
        key = self.session_key(raw_session_id)
        session = sessions.get(key)
        if session is None:
            session = Session(
                session_id=key,
                cwd=repo_real,
                transcript_path=str(path),
                start=ts,
                end=ts,
            )
            sessions[key] = session
        session.start = min(session.start, ts)
        session.end = max(session.end, ts)
        return session

    def note_edit(self, session: Session, file_path) -> None:
        if isinstance(file_path, str) and file_path and file_path not in session.edited_files:
            session.edited_files.append(file_path)

    def note_command(self, session: Session, command) -> None:
        if isinstance(command, str) and _commit_command_re().search(command):
            session.ran_git_commit = True


def relpath_inside(file_path: str, repo_real: str) -> str | None:
    normalized = normalize_path(file_path)
    try:
        rel = os.path.relpath(normalized, repo_real)
    except ValueError:  # different drive on Windows
        return None
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def group_edit_files_by_repo(sessions: list[Session], repo_real: str) -> None:
    """Normalize session edited-file paths to repo-relative POSIX paths in place.

    Paths outside the repository are dropped: they cannot be attributed to
    commits inside it. Mutates ``Session.edited_files``.
    """
    for session in sessions:
        relative: list[str] = []
        for file_path in session.edited_files:
            rel = relpath_inside(file_path, repo_real)
            if rel is not None and rel not in relative:
                relative.append(rel)
        session.edited_files = relative


def sessions_by_id(sessions: list[Session]) -> dict[str, Session]:
    return {s.session_id: s for s in sessions}


def count_records(sessions: list[Session]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for session in sessions:
        totals["sessions"] += 1
        totals["api_calls"] += len(session.api_calls)
        totals["tool_uses"] += len(session.tool_uses)
    return dict(totals)


def record_timestamp(record: dict):
    return parse_iso8601(record.get("timestamp") or "")
