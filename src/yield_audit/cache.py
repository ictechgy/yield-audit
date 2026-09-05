"""Persistent, content-addressed cache for immutable git facts.

Blame counts (``(ref, path) -> {sha: lines}``) and tree listings
(``ref -> files``) are functions of immutable git objects — once computed
they can never change for that ref. Caching them across runs is therefore
safe by construction: no invalidation, no staleness, output identical.

What is deliberately NOT cached: snapshot refs and the touch map — both
depend on wall-clock windows and change as new commits land.

Storage: one JSON file per audited repository under
``$YIELD_AUDIT_CACHE_DIR`` (default ``~/.cache/yield-audit``), keyed by a
hash of the repository's real path. Repositories themselves are never
written to. All I/O is best-effort: a missing, corrupt, or unwritable
cache silently degrades to computing from scratch.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

_TREE_PREFIX = "__tree__"
_SEP = "\x1f"
_MAX_ENTRIES = 200_000  # guard: a pathological repo should not grow this unbounded


def cache_root() -> Path:
    override = os.environ.get("YIELD_AUDIT_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "yield-audit"


def cache_path(repo_real: str) -> Path:
    digest = hashlib.sha1(repo_real.encode("utf-8", errors="replace")).hexdigest()[:16]
    return cache_root() / digest / "git-facts.json"


def load(repo_real: str) -> dict:
    """Immutable git facts for ``repo_real`` as an in-process blame cache.

    Tuple keys are re-created from their serialized string form; anything
    unexpected (missing file, bad JSON, wrong shapes) yields an empty
    cache.
    """
    try:
        raw = json.loads(cache_path(repo_real).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if not isinstance(key, str) or _SEP not in key:
            continue
        head, _, tail = key.partition(_SEP)
        if head == _TREE_PREFIX:
            if isinstance(value, list):
                out[(_TREE_PREFIX, tail)] = {v for v in value if isinstance(v, str)}
        elif head.startswith("__"):
            continue  # reserved per-run keys (snapshots, touch maps) never persist
        elif isinstance(value, dict):
            out[(head, tail)] = {
                sha: count
                for sha, count in value.items()
                if isinstance(sha, str) and isinstance(count, int)
            }
    return out


def save(repo_real: str, cache: dict) -> bool:
    """Persist the cache's immutable entries atomically. Best-effort."""
    entries: dict[str, object] = {}
    for key, value in cache.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        head, tail = key
        if head == _TREE_PREFIX:
            entries[_TREE_PREFIX + _SEP + tail] = sorted(value)
        elif head.startswith("__"):
            continue  # reserved per-run keys (snapshots, touch maps) never persist
        elif isinstance(value, dict):
            entries[head + _SEP + tail] = value
    if not entries:
        return False
    target = cache_path(repo_real)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # keep the file bounded: newest-first is not tracked (dicts are
        # insertion-ordered = roughly discovery order), so hard-truncate
        payload = dict(list(entries.items())[:_MAX_ENTRIES])
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, prefix="git-facts.", delete=False
        ) as handle:
            json.dump(payload, handle, separators=(",", ":"))
            os.replace(handle.name, target)
    except OSError:
        return False
    return True
