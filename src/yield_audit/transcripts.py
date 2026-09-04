"""Claude Code transcript adapter.

Reads the local JSONL session logs (``~/.claude/projects/<munged-cwd>/<session>.jsonl``)
and normalizes them into :class:`yield_audit.events.Session` objects. The adapter is
key-based and defensive: unknown record types, missing fields, and malformed lines are
skipped rather than trusted. No message content is interpreted beyond what the event
model needs (tool names, file paths of edits, commands, error flags, usage numbers).

Schema grounding (observed 2026-09 across client versions 2.1.222–2.1.260):

- ``user``/``assistant`` records carry an envelope: ``sessionId``, ``timestamp``
  (ISO-8601 UTC), ``cwd``, ``isSidechain``, ``version``.
- ``assistant.message.usage`` carries ``input_tokens``, ``output_tokens``,
  ``cache_read_input_tokens``, ``cache_creation_input_tokens``.
- ``assistant.message.content`` items of type ``tool_use`` carry ``id``/``name``/``input``.
- The matching ``tool_result`` (``tool_use_id``) arrives on the next ``user`` record;
  ``is_error`` flags failures.
- ``system`` records with ``subtype == "compact_boundary"`` mark compaction events.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from .events import ApiCall, Session, ToolResult, ToolUse, parse_iso8601

EDIT_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}
READ_TOOLS = {"Read"}
COMMIT_COMMAND_RE = None  # compiled lazily to keep import time near zero


def _commit_command_re():
    global COMMIT_COMMAND_RE
    if COMMIT_COMMAND_RE is None:
        import re

        # "git commit" as an actual argument, not e.g. "git commitlint".
        COMMIT_COMMAND_RE = re.compile(r"(?:^|[\s;&|(])git(?:\s+-[^\s]+\s+)*\s*commit(?:\s|$)")
    return COMMIT_COMMAND_RE


def default_transcripts_root() -> Path:
    return Path.home() / ".claude" / "projects"


def load_sessions(
    repo: str,
    transcripts_root: Path,
    *,
    now,
    days: int,
    logger=None,
) -> list[Session]:
    """Return sessions whose ``cwd`` matches ``repo``, ending within the last ``days``.

    Sidechain records (subagent transcripts) are excluded: their API calls are billed
    under the parent conversation's account but inflate per-session noise without
    changing commit attribution. Set ``days`` <= 0 to disable the time filter.
    """
    repo_real = _normalize_path(repo)
    cutoff = None
    if days and days > 0:
        from datetime import timedelta

        cutoff = now - timedelta(days=days)

    sessions: dict[str, Session] = {}
    found_any_jsonl = False
    for jsonl in sorted(transcripts_root.rglob("*.jsonl")):
        found_any_jsonl = True
        try:
            _ingest_file(jsonl, repo_real, sessions)
        except (OSError, UnicodeDecodeError) as exc:
            if logger:
                logger(f"skip unreadable transcript {jsonl.name}: {exc.__class__.__name__}")
    if not found_any_jsonl and logger:
        logger(f"no *.jsonl transcripts found under {transcripts_root}")

    selected = []
    for session in sessions.values():
        if session.cwd != repo_real:
            continue
        if cutoff is not None and session.end < cutoff:
            continue
        selected.append(session)
    selected.sort(key=lambda s: s.start)
    return selected


def _normalize_path(path: str) -> str:
    return normalize_path(path)


def normalize_path(path: str) -> str:
    try:
        return os.path.realpath(str(path))
    except OSError:
        return str(path)


def _ingest_file(jsonl_path: Path, repo_real: str, sessions: dict[str, Session]) -> None:
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            _ingest_record(record, jsonl_path, repo_real, sessions)


def _ingest_record(record: dict, path: Path, repo_real: str, sessions: dict[str, Session]) -> None:
    rtype = record.get("type")
    if rtype not in ("user", "assistant", "system"):
        return
    if record.get("isSidechain") is True:
        return
    session_id = record.get("sessionId") or record.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    ts = parse_iso8601(record.get("timestamp") or "")
    if ts is None:
        return

    session = sessions.get(session_id)
    if session is None:
        cwd = record.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return
        if _normalize_path(cwd) != repo_real:
            return
        session = Session(
            session_id=session_id,
            cwd=repo_real,
            transcript_path=str(path),
            start=ts,
            end=ts,
        )
        sessions[session_id] = session

    session.start = min(session.start, ts)
    session.end = max(session.end, ts)

    if rtype == "system":
        if record.get("subtype") == "compact_boundary":
            session.compact_boundaries.append(ts)
        return

    message = record.get("message")
    if not isinstance(message, dict):
        return
    if rtype == "assistant":
        _ingest_assistant(record, message, ts, session)
    else:
        _ingest_user(record, message, ts, session)


def _int(value) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _ingest_assistant(record: dict, message: dict, ts, session: Session) -> None:
    usage = message.get("usage")
    if isinstance(usage, dict):
        model = message.get("model")
        session.api_calls.append(
            ApiCall(
                ts=ts,
                model=model if isinstance(model, str) and model else "unknown",
                input_tokens=_int(usage.get("input_tokens")),
                output_tokens=_int(usage.get("output_tokens")),
                cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
                cache_write_tokens=_int(usage.get("cache_creation_input_tokens")),
            )
        )
    content = message.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        use_id = item.get("id")
        name = item.get("name")
        if not isinstance(use_id, str) or not isinstance(name, str):
            continue
        tool_input = item.get("input")
        use = ToolUse(
            id=use_id,
            ts=ts,
            name=name,
            input=tool_input if isinstance(tool_input, dict) else {},
        )
        session.tool_uses.append(use)
        if name in EDIT_TOOLS:
            file_path = use.input.get("file_path") or use.input.get("notebook_path")
            if isinstance(file_path, str) and file_path and file_path not in session.edited_files:
                session.edited_files.append(file_path)
        if name == "Bash":
            command = use.input.get("command")
            if isinstance(command, str) and _commit_command_re().search(command):
                session.ran_git_commit = True


def _ingest_user(record: dict, message: dict, ts, session: Session) -> None:
    content = message.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_result":
            continue
        use_id = item.get("tool_use_id")
        if not isinstance(use_id, str) or not use_id:
            continue
        session.tool_results[use_id] = ToolResult(
            tool_use_id=use_id,
            ts=ts,
            is_error=bool(item.get("is_error")),
        )


def sessions_by_id(sessions: list[Session]) -> dict[str, Session]:
    return {s.session_id: s for s in sessions}


def group_edit_files_by_repo(sessions: list[Session], repo_real: str) -> None:
    """Normalize session edited-file paths to repo-relative POSIX paths in place.

    Paths outside the repository are dropped: they cannot be attributed to commits
    inside it. Mutates ``Session.edited_files``.
    """
    for session in sessions:
        relative: list[str] = []
        for file_path in session.edited_files:
            rel = relpath_inside(file_path, repo_real)
            if rel is not None and rel not in relative:
                relative.append(rel)
        session.edited_files = relative


def relpath_inside(file_path: str, repo_real: str) -> str | None:
    normalized = _normalize_path(file_path)
    try:
        rel = os.path.relpath(normalized, repo_real)
    except ValueError:  # different drive on Windows
        return None
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def count_records(sessions: list[Session]) -> dict[str, int]:
    totals: defaultdict[str, int] = defaultdict(int)
    for session in sessions:
        totals["sessions"] += 1
        totals["api_calls"] += len(session.api_calls)
        totals["tool_uses"] += len(session.tool_uses)
    return dict(totals)
