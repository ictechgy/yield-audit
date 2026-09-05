"""Codex CLI transcript adapter.

Reads the local JSONL "rollout" session logs (``~/.codex/sessions/YYYY/MM/DD/
rollout-<ts>-<uuid>.jsonl``) produced by the OpenAI Codex CLI (codex-rs) and
normalizes them into :class:`yield_audit.events.Session` objects.

Schema grounding (rollout format observed 2026-09; parsed defensively — when
a future client renames keys, records degrade to skips, never crashes):

- Every record carries ``timestamp`` (ISO-8601 UTC) and a ``type``:
  ``session_meta``, ``turn_context``, ``response_item``, or ``event_msg``.
- ``session_meta.payload`` carries the rollout's ``id`` and ``cwd``.
- ``turn_context.payload`` repeats ``cwd`` and carries the ``model`` in
  effect for subsequent turns.
- ``response_item.payload`` is a Chat-Completions-style item:
  ``function_call`` (``call_id``/``name``/``arguments``) and
  ``function_call_output`` (``call_id``/``output``).
- ``event_msg.payload`` of type ``token_count`` carries ``info.last_token_usage``
  (``input_tokens``, ``cached_input_tokens``, ``output_tokens``).

Vendor tool names are normalized to the canonical set every lens understands:
shell execution (``shell``/``local_shell``/``exec_command``) becomes ``Bash``
with a joined command string; ``apply_patch`` edits are folded into
``Session.edited_files`` via the patch's ``*** (Add|Update|Delete) File``
headers. Compaction boundaries are not emitted by this format and stay empty.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from ..events import ApiCall, Session, ToolResult, ToolUse, parse_iso8601
from .base import TranscriptAdapter, _int, normalize_path

SHELL_TOOLS = {"shell", "local_shell", "exec_command"}
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def _date_pruned_files(root: Path, since) -> list[Path] | None:
    """``*.jsonl`` under valid ``YYYY/MM/DD`` day-directories at/after
    ``since.date()``. ``None`` means the root doesn't follow the date
    layout (no valid date directory at all) — caller falls back to a
    full walk. An empty list means the layout exists but every day is
    older than the window.
    """
    out: list[Path] = []
    saw_date_dir = False
    try:
        year_dirs = [p for p in root.iterdir() if p.is_dir() and len(p.name) == 4 and p.name.isdigit()]
    except OSError:
        return None
    for year in sorted(year_dirs):
        for month in sorted(p for p in _safe_iterdir(year) if p.is_dir() and len(p.name) == 2 and p.name.isdigit()):
            for day in sorted(
                p for p in _safe_iterdir(month) if p.is_dir() and len(p.name) == 2 and p.name.isdigit()
            ):
                try:
                    dir_date = date(int(year.name), int(month.name), int(day.name))
                except ValueError:
                    continue
                saw_date_dir = True
                if dir_date < since.date():
                    continue
                out.extend(sorted(day.glob("*.jsonl")))
    return out if saw_date_dir else None


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


class CodexAdapter(TranscriptAdapter):
    name = "codex"

    def default_root(self) -> Path:
        return Path.home() / ".codex" / "sessions"

    def iter_files(self, root: Path, repo_real: str, logger=None, since=None) -> list[Path]:
        """Prune the date-partitioned layout (``YYYY/MM/DD``) by window start.

        Codex stores sessions under ``sessions/<year>/<month>/<day>/``; when
        that layout is present, day-directories older than ``since`` are
        skipped without even listing them. Roots that don't follow the
        layout fall back to the base full walk.
        """
        if since is not None:
            files = _date_pruned_files(root, since)
            if files is not None:
                if logger:
                    logger(f"[{self.name}] scanning {root} pruned to >= {since.date().isoformat()} ({len(files)} files)")
                return files
        return super().iter_files(root, repo_real, logger, since)

    def handle_record(
        self, record: dict, ctx: dict, path: Path, repo_real: str, sessions: dict[str, Session]
    ) -> None | bool:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        ts = parse_iso8601(record.get("timestamp") or "")
        if ts is None:
            return
        rtype = record.get("type")

        if rtype == "session_meta":
            session_id = payload.get("id")
            if isinstance(session_id, str) and session_id:
                ctx["session_id"] = session_id
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                ctx["cwd"] = cwd
            # session_meta opens every rollout file; once its cwd provably
            # points elsewhere, the whole file is another project's — stop.
            known_cwd = ctx.get("cwd")
            if isinstance(known_cwd, str) and normalize_path(known_cwd) != repo_real:
                return False
            return
        if rtype == "turn_context":
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                ctx["cwd"] = cwd
            model = payload.get("model")
            if isinstance(model, str) and model:
                ctx["model"] = model
            return

        session_id = ctx.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return
        session = self.ensure_session(sessions, session_id, ctx.get("cwd"), repo_real, ts, path)
        if session is None:
            return

        if rtype == "event_msg":
            self._ingest_event(payload, ctx, ts, session)
        elif rtype == "response_item":
            self._ingest_response_item(payload, ts, session)

    def _ingest_event(self, payload: dict, ctx: dict, ts, session: Session) -> None:
        if payload.get("type") != "token_count":
            return
        info = payload.get("info")
        if not isinstance(info, dict):
            return
        usage = info.get("last_token_usage")
        if not isinstance(usage, dict):
            usage = info.get("total_token_usage")
        if not isinstance(usage, dict):
            return
        session.api_calls.append(
            ApiCall(
                ts=ts,
                model=ctx.get("model") if isinstance(ctx.get("model"), str) else "unknown",
                input_tokens=_int(usage.get("input_tokens")),
                output_tokens=_int(usage.get("output_tokens")),
                cache_read_tokens=_int(usage.get("cached_input_tokens")),
                cache_write_tokens=0,
            )
        )

    def _ingest_response_item(self, payload: dict, ts, session: Session) -> None:
        ptype = payload.get("type")
        if ptype == "function_call":
            call_id = payload.get("call_id")
            name = payload.get("name")
            if not isinstance(call_id, str) or not isinstance(name, str):
                return
            args = _parse_arguments(payload.get("arguments"))
            if name in SHELL_TOOLS:
                command = _shell_command(args)
                session.tool_uses.append(
                    ToolUse(id=call_id, ts=ts, name="Bash", input={"command": command} if command else {})
                )
                if command:
                    self.note_command(session, command)
            elif name == "apply_patch":
                session.tool_uses.append(ToolUse(id=call_id, ts=ts, name=name, input={}))
                for file_path in _patch_files(args):
                    self.note_edit(session, file_path)
            else:
                session.tool_uses.append(ToolUse(id=call_id, ts=ts, name=name, input=args))
        elif ptype == "function_call_output":
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return
            session.tool_results[call_id] = ToolResult(
                tool_use_id=call_id,
                ts=ts,
                is_error=_output_is_error(payload.get("output")),
            )


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _shell_command(args: dict) -> str | None:
    """Shell tool command as a single string, whichever key the client used."""
    for key in ("command", "cmd"):
        value = args.get(key)
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        if isinstance(value, str) and value:
            return value
    return None


def _patch_files(args: dict) -> list[str]:
    """File paths touched by an apply_patch call, in patch order.

    The patch text arrives wrapped in an ``input``/``patch``/``value`` JSON
    field depending on client version; anything without a patch header
    yields no files.
    """
    text = None
    for key in ("input", "patch", "value"):
        value = args.get(key)
        if isinstance(value, str) and "*** Begin Patch" in value:
            text = value
            break
    if text is None:
        return []
    return [match.strip() for match in PATCH_FILE_RE.findall(text)]


def _output_is_error(output) -> bool:
    """Shell failures surface as a nonzero ``exit_code`` in the output envelope."""
    if not isinstance(output, str) or not output:
        return False
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    for candidate in (parsed.get("metadata"), parsed):
        if isinstance(candidate, dict) and isinstance(candidate.get("exit_code"), (int, float)):
            return candidate["exit_code"] != 0
    return False
