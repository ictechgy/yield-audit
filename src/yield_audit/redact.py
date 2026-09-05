"""Output-boundary sanitization and redaction.

Transcript-derived strings (commands, model names, paths, session ids) are
semi-trusted: an agent can write arbitrary bytes into its own transcript.
Everything that reaches a report passes through this module first, so the
promises in the README — terminal-safe output, paste-safe markdown, paths
redacted by default — hold at a single choke point instead of being scattered
across renderers. ``audit.run_audit`` additionally deep-walks the finished
report with :func:`sanitize_text` as defense in depth, so a field that
forgets to sanitize cannot emit raw bytes.
"""

from __future__ import annotations

import os
import re

# CSI sequences (including intermediate bytes 0x20-0x2f), OSC sequences
# (terminated by BEL or ST), and any other escape introduction we did not
# anticipate.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?<>\x20-\x2f]*[A-Za-z]"      # CSI: cursor/color/erase
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"      # OSC: window title, hyperlinks
    r"|\x1b[a-zA-Z0-9=><]"                      # any other two-byte escape
)
# ASCII C0 plus the C1 range (U+0080-U+009F), which some terminals treat as
# control codes even in UTF-8 mode (e.g. raw 8-bit CSI).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\u0080-\u009f]")

# Absolute POSIX paths in free text: "/"-prefixed runs. The lookbehind is
# deliberately ASCII-only: a Unicode \w (e.g. Hangul) before a path must not
# suppress redaction. ":" and "/" exclusions keep URLs (https://, //) intact.
_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_:~/])/(?:[^\s/]+/)+[^\s]*")
# Home-relative paths in free text: "~/secret/key.pem" leaks just as much.
_HOME_PATH_RE = re.compile(r"(?<![A-Za-z0-9_:~/])~/(?:[^\s/]+/)+[^\s]*")

PATH_PLACEHOLDER = "<path>"


def sanitize_text(value) -> str:
    """Strip ANSI escapes and control characters (replaced by spaces); non-strings become ''."""
    if not isinstance(value, str):
        return ""
    return _CONTROL_RE.sub(" ", _ANSI_RE.sub("", value))


def redact_absolute_paths(text: str) -> str:
    """Replace absolute and home-relative path substrings with ``<path>``.

    Input must already be sanitized. Windows UNC paths (``\\\\host\\share``)
    are a documented gap: they contain no "/" and are not matched.
    """
    return _HOME_PATH_RE.sub(PATH_PLACEHOLDER, _ABS_PATH_RE.sub(PATH_PLACEHOLDER, text))


def sanitize_command(command: str, *, show_paths: bool, limit: int = 120) -> str:
    """Transcript command -> safe report string.

    Control/ANSI sequences are always removed; absolute and ``~/`` paths are
    redacted unless ``show_paths``.
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


def deep_sanitize(value):
    """Recursively sanitize every string in a report-shaped structure.

    Field-level redaction stays where it is; this is the safety net that
    makes a forgotten sanitize call unable to emit raw bytes. Dict keys are
    sanitized too (report keys include transcript-derived session ids).
    Idempotent on already-sanitized strings.
    """
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {
            sanitize_text(k) if isinstance(k, str) else k: deep_sanitize(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [deep_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(deep_sanitize(item) for item in value)
    return value
