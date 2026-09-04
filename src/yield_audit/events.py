"""Event model shared by every yield-audit lens.

The model is intentionally small: transcripts are normalized into
``Session`` objects containing ``ApiCall`` (one per assistant message,
which is one provider API request), ``ToolUse``/``ToolResult`` pairs,
edited-file paths, and compact-boundary timestamps. Every lens is a
pure function over these structures so results stay deterministic and
testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ApiCall:
    """One assistant message = one provider API request."""

    ts: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class ToolUse:
    id: str
    ts: datetime
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_use_id: str
    ts: datetime
    is_error: bool


@dataclass
class Session:
    session_id: str
    cwd: str
    transcript_path: str
    start: datetime
    end: datetime
    api_calls: list[ApiCall] = field(default_factory=list)
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)  # by tool_use_id
    edited_files: list[str] = field(default_factory=list)  # absolute paths, first-seen order
    ran_git_commit: bool = False
    compact_boundaries: list[datetime] = field(default_factory=list)

    def bash_sequence(self) -> list[tuple[datetime, str, bool]]:
        """Chronological (ts, normalized-command, is_error) for Bash tool uses."""
        out: list[tuple[datetime, str, bool]] = []
        for use in self.tool_uses:
            if use.name != "Bash":
                continue
            command = use.input.get("command") if isinstance(use.input, dict) else None
            if not isinstance(command, str):
                continue
            result = self.tool_results.get(use.id)
            is_error = bool(result.is_error) if result is not None else False
            out.append((use.ts, normalize_command(command), is_error))
        out.sort(key=lambda item: item[0])
        return out

    def verification_commands(self, verify_pattern) -> list[datetime]:
        """Timestamps of Bash commands matching a compiled verification regex."""
        stamps: list[datetime] = []
        for use in self.tool_uses:
            if use.name != "Bash":
                continue
            command = use.input.get("command") if isinstance(use.input, dict) else None
            if isinstance(command, str) and verify_pattern.search(command):
                stamps.append(use.ts)
        return sorted(stamps)


def normalize_command(command: str) -> str:
    """Whitespace-insensitive command normalization for repeat detection."""
    return " ".join(command.split())


def parse_iso8601(value: str) -> datetime | None:
    """Parse the transcripts' ISO-8601 UTC timestamps (``...Z``) on py3.10+."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
