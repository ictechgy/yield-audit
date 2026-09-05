"""Output-boundary sanitization and redaction.

Transcript-derived strings (commands, model names, paths) are semi-trusted:
an agent can write arbitrary bytes into its own transcript. Everything that
reaches a report passes through this module first, so the promises in the
README — terminal-safe output, paste-safe markdown, paths redacted by
default — hold at a single choke point instead of being scattered across
renderers.
"""

from __future__ import annotations

import os
import re

# CSI sequences, OSC sequences (terminated by BEL or ST), and any other
# escape introduction we did not anticipate.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"       # CSI: cursor/color/erase
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: window title, hyperlinks
    r"|\x1b[a-zA-Z0-9=><]"           # any other two-byte escape
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Absolute POSIX paths in free text: "/"-prefixed runs not preceded by a
# word char, "~", ":" (URL scheme), or "/" (URL "//" and protocol-relative).
_ABS_PATH_RE = re.compile(r"(?<![\w:~/])/(?:[^\s/]+/)+[^\s]*")

PATH_PLACEHOLDER = "<path>"


def sanitize_text(value) -> str:
    """Strip ANSI escapes and control characters; non-strings become ''."""
    if not isinstance(value, str):
        return ""
    return _CONTROL_RE.sub(" ", _ANSI_RE.sub("", value))


def redact_absolute_paths(text: str) -> str:
    """Replace absolute path substrings with ``<path>`` (text already sanitized)."""
    return _ABS_PATH_RE.sub(PATH_PLACEHOLDER, text)


def sanitize_command(command: str, *, show_paths: bool, limit: int = 120) -> str:
    """Transcript command -> safe report string.

    Control/ANSI sequences are always removed; absolute paths are redacted
    unless ``show_paths``.
    """
    cleaned = sanitize_text(command)
    if not show_paths:
        cleaned = redact_absolute_paths(cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


def redact_path(path: str, *, show_paths: bool = False) -> str:
    """Basename-only redaction that also handles Windows separators."""
    if show_paths:
        return path
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def abbreviate_home(path: str) -> str:
    """``/home/user/x`` -> ``~/x`` so reports do not embed the home path."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    for sep in (os.sep, "/"):
        if path.startswith(home + sep):
            return "~" + path[len(home):]
    return path


def markdown_cell(value: str) -> str:
    """Make a sanitized string safe inside a markdown table cell / code span."""
    return (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("|", "\\|")
    )
