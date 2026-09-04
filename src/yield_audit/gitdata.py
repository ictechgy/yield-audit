"""Thin read-only git wrapper.

Every command runs with ``cwd=repo`` and is strictly read-only (log, show,
blame, rev-list, cat-file). Failures raise :class:`GitError` with the
command and stderr so callers can degrade honestly instead of guessing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime


class GitError(RuntimeError):
    pass


@dataclass
class CommitInfo:
    sha: str
    date: datetime
    summary: str
    files: dict[str, int]  # path -> added lines (binary files excluded)


def _run(repo: str, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:  # git not installed
        raise GitError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().splitlines()
        detail = stderr[0] if stderr else f"exit code {proc.returncode}"
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def is_git_repo(repo: str) -> bool:
    try:
        _run(repo, ["rev-parse", "--is-inside-work-tree"])
        return True
    except GitError:
        return False


def _has_commits(repo: str) -> bool:
    try:
        _run(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])
        return True
    except GitError:
        return False


def commits_with_numstat(repo: str, since: datetime | None, until: datetime | None) -> list[CommitInfo]:
    """Commits in [since, until] with per-file added-line counts.

    Merge commits naturally contribute no numstat rows and therefore zero added
    lines; parent/first-parent semantics do not matter for added-line accounting.
    """
    if not _has_commits(repo):
        return []
    fmt = "@@@%H%x1f%aI%x1f%s"
    args = ["log", f"--pretty=format:{fmt}", "--numstat", "--no-renames"]
    if since is not None:
        args.append(f"--since={since.isoformat()}")
    if until is not None:
        args.append(f"--until={until.isoformat()}")
    out = _run(repo, args)

    commits: list[CommitInfo] = []
    current: CommitInfo | None = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            if current is not None:
                commits.append(current)
            parts = line[3:].split("\x1f")
            date = _parse_git_date(parts[1] if len(parts) > 1 else "")
            current = CommitInfo(
                sha=parts[0] if parts else "",
                date=date,
                summary=parts[2] if len(parts) > 2 else "",
                files={},
            )
        elif current is not None and "\t" in line:
            cols = line.split("\t")
            if len(cols) >= 3 and cols[0].isdigit():
                path = "/".join(cols[2:]).strip()
                if path:
                    current.files[path] = int(cols[0])
    if current is not None:
        commits.append(current)
    return commits


def _parse_git_date(value: str) -> datetime:
    from .events import parse_iso8601

    parsed = parse_iso8601(value)
    if parsed is None:
        raise GitError(f"unparseable commit date {value!r}")
    return parsed


def snapshot_ref(repo: str, target_date: datetime) -> str:
    """The commit that was HEAD at ``target_date`` (last commit <= date), else HEAD."""
    out = _run(repo, ["rev-list", "-1", f"--before={target_date.isoformat()}", "HEAD"]).strip()
    if out:
        return out
    return _run(repo, ["rev-parse", "HEAD"]).strip()


def file_exists_at(repo: str, ref: str, path: str) -> bool:
    try:
        _run(repo, ["cat-file", "-e", f"{ref}:{path}"])
        return True
    except GitError:
        return False


def blame_line_shas(repo: str, ref: str, path: str) -> list[str]:
    """Per-current-line origin SHA at ``ref`` (porcelain blame, first header line only)."""
    out = _run(repo, ["blame", "-l", "--porcelain", ref, "--", path])
    shas: list[str] = []
    for line in out.splitlines():
        if not line:
            continue
        first = line.split(" ", 1)[0]
        if len(first) >= 40 and all(c in "0123456789abcdef" for c in first):
            shas.append(first)
    return shas


def head_sha(repo: str) -> str:
    return _run(repo, ["rev-parse", "HEAD"]).strip()


def resolve_short(repo: str, sha: str) -> str:
    try:
        return _run(repo, ["rev-parse", "--short", sha]).strip()
    except GitError:
        return sha[:8]
