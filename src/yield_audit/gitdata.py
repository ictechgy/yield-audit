"""Thin read-only git wrapper.

Every command runs with ``cwd=repo`` and is strictly read-only (log, show,
blame, rev-list, cat-file, ls-tree). The subprocess environment is stripped
of ``GIT_*`` variables so a stray ``GIT_DIR`` in the user's shell cannot
silently redirect the audit at a different repository. Failures raise
:class:`GitError` with the command and stderr so callers can degrade
honestly instead of guessing.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime

_HEX = set("0123456789abcdef")


class GitError(RuntimeError):
    pass


@dataclass
class CommitInfo:
    sha: str
    date: datetime
    summary: str
    files: dict[str, int]  # path -> added lines (binary files excluded)


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _run(repo: str, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=_clean_env(),
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


def commits_with_numstat(
    repo: str,
    since: datetime | None,
    until: datetime | None,
    warnings: list[str] | None = None,
) -> list[CommitInfo]:
    """Commits in [since, until] with per-file added-line counts.

    Streams git output line by line (full history with ``--days 0`` can be
    hundreds of MB). Commits whose date cannot be parsed are skipped with a
    warning instead of aborting the audit. Merge commits contribute no
    numstat rows and therefore zero added lines. ``core.quotePath=false``
    keeps non-ASCII paths readable; a literal newline inside a path remains
    a documented v0.1 limitation.
    """
    if not _has_commits(repo):
        return []
    fmt = "@@@%H%x1f%aI%x1f%s"
    args = [
        "-c", "core.quotePath=false",
        "log", f"--pretty=format:{fmt}", "--numstat", "--no-renames",
    ]
    if since is not None:
        args.append(f"--since={since.isoformat()}")
    if until is not None:
        args.append(f"--until={until.isoformat()}")

    commits: list[CommitInfo] = []
    current: CommitInfo | None = None
    skip_current = False
    try:
        proc = subprocess.Popen(
            ["git", "-C", repo, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_clean_env(),
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc

    assert proc.stdout is not None
    with proc.stdout:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("@@@"):
                if current is not None:
                    commits.append(current)
                current = None
                skip_current = False
                parts = line[3:].split("\x1f")
                sha = parts[0] if parts else ""
                try:
                    date = _parse_git_date(parts[1] if len(parts) > 1 else "")
                except GitError:
                    if warnings is not None:
                        warnings.append(f"skipped commit {sha[:8]}: unparseable date")
                    skip_current = True
                    continue
                current = CommitInfo(
                    sha=sha,
                    date=date,
                    summary=parts[2] if len(parts) > 2 else "",
                    files={},
                )
            elif skip_current:
                continue
            elif current is not None and "\t" in line:
                cols = line.split("\t")
                if len(cols) >= 3 and cols[0].isdigit():
                    path = "/".join(cols[2:])
                    if path:
                        current.files[path] = int(cols[0])
    if proc.wait() != 0:
        raise GitError(f"git log failed with exit code {proc.returncode}")
    if current is not None:
        commits.append(current)
    return commits


def _parse_git_date(value: str) -> datetime:
    from .events import parse_iso8601

    parsed = parse_iso8601(value)
    if parsed is None:
        raise GitError(f"unparseable commit date {value!r}")
    return parsed


def commit_messages(repo: str, since: datetime | None, until: datetime | None) -> dict[str, str]:
    """Full commit messages (subject + body) keyed by sha, for cohort evidence.

    AI authorship footers (``Co-Authored-By: …``, ``🤖 Generated with …``)
    live in the body, which the numstat stream never carries — hence this
    second, message-only pass over the same window. Records are terminated
    by ``\\x1e`` so multi-line bodies survive line-oriented transport; a
    body containing a literal ``\\x1e`` would truncate its own record, a
    documented limitation (it corrupts evidence for that one commit, never
    the pipeline).
    """
    if not _has_commits(repo):
        return {}
    args = [
        "-c", "core.quotePath=false",
        "log", "--pretty=format:%H%x1f%B%x1e",
    ]
    if since is not None:
        args.append(f"--since={since.isoformat()}")
    if until is not None:
        args.append(f"--until={until.isoformat()}")

    out = _run(repo, args)
    messages: dict[str, str] = {}
    for record in out.split("\x1e"):
        record = record.lstrip("\n")
        if not record:
            continue
        sha, sep, body = record.partition("\x1f")
        sha = sha.strip()
        if sep and len(sha) >= 40 and set(sha) <= _HEX:
            messages[sha] = body.strip("\n")
    return messages


def snapshot_ref(repo: str, target_date: datetime) -> str:
    """The commit that was HEAD at ``target_date`` (last commit <= date), else HEAD."""
    out = _run(repo, ["rev-list", "-1", f"--before={target_date.isoformat()}", "HEAD"]).strip()
    if out:
        return out
    return _run(repo, ["rev-parse", "HEAD"]).strip()


def tree_files(repo: str, ref: str) -> set[str]:
    """All file paths present at ``ref`` — one process instead of N cat-files."""
    out = _run(repo, ["ls-tree", "-r", "--name-only", ref])
    return {line for line in out.splitlines() if line}


def blame_sha_counts(repo: str, ref: str, path: str) -> dict[str, int]:
    """Per-current-line origin SHA counts at ``ref`` (streamed; counts only).

    Only ``sha == attributed commit`` comparisons are ever needed downstream,
    so the per-line list collapses to a counter — blame output for large
    files otherwise dominates memory (porcelain includes file content).
    """
    try:
        proc = subprocess.Popen(
            ["git", "-C", repo, "blame", "-l", "--porcelain", ref, "--", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_clean_env(),
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    assert proc.stdout is not None
    with proc.stdout:
        counts = counts_from_porcelain(proc.stdout)
    if proc.wait() != 0:
        raise GitError(f"git blame {ref}:{path} failed with exit code {proc.returncode}")
    return counts


def counts_from_porcelain(lines) -> dict[str, int]:
    """Count origin SHAs from ``git blame --porcelain`` output.

    Only lines that *start* with a 40-hex token are line headers; content
    lines (tab-prefixed) and metadata lines (``previous``, ``boundary``) must
    not be counted even when their first token looks like a SHA.
    """
    counts: dict[str, int] = {}
    for line in lines:
        if not line or line.startswith("\t"):
            continue
        first = line.split(" ", 1)[0].strip()
        if len(first) >= 40 and set(first) <= _HEX:
            counts[first] = counts.get(first, 0) + 1
    return counts


def head_sha(repo: str) -> str:
    return _run(repo, ["rev-parse", "HEAD"]).strip()


def resolve_short(repo: str, sha: str) -> str:
    try:
        return _run(repo, ["rev-parse", "--short", sha]).strip()
    except GitError:
        return sha[:8]
