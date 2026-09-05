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

import re
from pathlib import Path

from ..events import ApiCall, Session, ToolResult, ToolUse, parse_iso8601
from .base import TranscriptAdapter, _int

EDIT_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}
READ_TOOLS = {"Read"}


class ClaudeAdapter(TranscriptAdapter):
    name = "claude"

    def default_root(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def iter_files(self, root: Path, repo_real: str, logger=None, since=None) -> list[Path]:
        """Transcript files to scan, newest-layout first.

        Claude Code stores a project's sessions under a directory named after its
        cwd with separators replaced by ``-``. When that directory exists we scan
        only it — a 100x+ shortcut over parsing every project's logs (sessions
        stored under other layouts, if any, are covered by the ``--transcripts-dir``
        override). Anything unusual (missing dir, unusual layout) falls back to a
        full walk.
        """
        candidate = root / munged_project_dir_name(repo_real)
        if candidate.is_dir():
            files = sorted(candidate.glob("*.jsonl"))
            if logger:
                logger(f"prefilter: scanning munged project dir {candidate.name} ({len(files)} files)")
            if files:
                return files
        return super().iter_files(root, repo_real, logger, since)

    def handle_record(
        self, record: dict, ctx: dict, path: Path, repo_real: str, sessions: dict[str, Session]
    ) -> None:
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

        session = sessions.get(self.session_key(session_id))
        if session is None:
            session = self.ensure_session(sessions, session_id, record.get("cwd"), repo_real, ts, path)
            if session is None:
                return

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
            self._ingest_assistant(message, ts, session)
        else:
            self._ingest_user(message, ts, session)

    def _ingest_assistant(self, message: dict, ts, session: Session) -> None:
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
                self.note_edit(session, file_path)
            if name == "Bash":
                self.note_command(session, use.input.get("command"))

    def _ingest_user(self, message: dict, ts, session: Session) -> None:
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


def munged_project_dir_name(repo_real: str) -> str:
    """Claude Code's project-dir name for a cwd: path separators (and the
    Windows drive colon) replaced by ``-``. Producing a separator-free name
    is also what keeps ``root / munged`` from resolving to an absolute path
    on backslash platforms.
    """
    return re.sub(r"[/\\:]", "-", repo_real)


def iter_transcript_files(root: Path, repo_real: str, logger=None) -> list[Path]:
    return ClaudeAdapter().iter_files(root, repo_real, logger)
