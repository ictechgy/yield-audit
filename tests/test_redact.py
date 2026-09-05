"""Output-boundary sanitization and redaction tests."""

from __future__ import annotations

from yield_audit.redact import (
    abbreviate_home,
    markdown_cell,
    redact_absolute_paths,
    redact_path,
    sanitize_command,
    sanitize_text,
)


def test_sanitize_strips_ansi_and_control_characters():
    hostile = "\x1b]0;pwned\x07npm \x1b[31mtest\x1b[0m\n\x07more"
    cleaned = sanitize_text(hostile)
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned
    assert "\n" not in cleaned
    assert "npm " in cleaned and "test" in cleaned


def test_sanitize_non_string_is_empty():
    assert sanitize_text(None) == ""
    assert sanitize_text(123) == ""


def test_redact_absolute_paths_keeps_urls():
    text = "cd /Users/you/secret-project && npm test https://example.com/a/b"
    redacted = redact_absolute_paths(text)
    assert "/Users/you" not in redacted
    assert "<path>" in redacted
    assert "https://example.com/a/b" in redacted  # URL must survive


def test_sanitize_command_gates_paths_on_show_paths():
    command = "cd /Users/you/secrets && pytest -q"
    assert sanitize_command(command, show_paths=False) == "cd <path> && pytest -q"
    assert "/Users/you/secrets" in sanitize_command(command, show_paths=True)


def test_redact_path_handles_windows_separators():
    assert redact_path(r"C:\Users\you\project\app.py") == "app.py"
    assert redact_path("/Users/you/project/app.py") == "app.py"
    assert redact_path("/Users/you/project/app.py", show_paths=True) == "/Users/you/project/app.py"


def test_abbreviate_home():
    import os

    home = os.path.expanduser("~")
    assert abbreviate_home(home) == "~"
    assert abbreviate_home(home + "/.claude/projects") == "~/.claude/projects"
    assert abbreviate_home("/elsewhere") == "/elsewhere"


def test_markdown_cell_escapes_breakouts():
    cell = markdown_cell("npm test `rm -rf /` |evil|")
    assert "`" not in cell.replace("\\`", "")
    assert "\\|" in cell
    assert "\\`" in cell
