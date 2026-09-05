"""Persistent git-facts cache: roundtrip, corruption tolerance, real reuse."""

from __future__ import annotations

import json

from conftest import NOW
from yield_audit import cache
from yield_audit.cli import main


def test_cache_roundtrip_only_keeps_immutable_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("YIELD_AUDIT_CACHE_DIR", str(tmp_path / "c"))
    blame = {("abc123", "src/x.py"): {"abc123": 12, "def456": 3}}
    tree = {("__tree__", "abc123"): {"src/x.py", "README.md"}}
    volatile = {
        ("__snapshot__", "2026-08-08T00:00:00+00:00"): "abc123",
        ("__touches__", "all"): {"src/x.py": [1, 2]},
    }
    assert cache.save("/repo", {**blame, **tree, **volatile}) == 2

    loaded = cache.load("/repo")
    assert loaded == {**blame, **tree}  # snapshot/touch entries dropped
    assert loaded[("__tree__", "abc123")] == {"src/x.py", "README.md"}


def test_cache_corrupt_or_missing_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("YIELD_AUDIT_CACHE_DIR", str(tmp_path / "c"))
    assert cache.load("/repo") == {}
    target = cache.cache_path("/repo")
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    assert cache.load("/repo") == {}
    target.write_text(json.dumps(["unexpected"]), encoding="utf-8")
    assert cache.load("/repo") == {}


def test_second_audit_reuses_persisted_blame(fixture_env, monkeypatch):
    from yield_audit import gitdata

    calls = {"n": 0}
    real_blame = gitdata.blame_sha_counts

    def counting_blame(repo, ref, path):
        calls["n"] += 1
        return real_blame(repo, ref, path)

    argv = [
        "audit",
        "--repo", fixture_env["repo_cwd"],
        "--transcripts-dir", str(fixture_env["transcripts_root"]),
        "--now", NOW,
        "--format", "json",
    ]
    monkeypatch.setattr(gitdata, "blame_sha_counts", counting_blame)
    assert main(argv) == 0
    first = calls["n"]
    assert cache.cache_path(fixture_env["repo_cwd"]).is_file()

    assert main(argv) == 0
    second = calls["n"] - first
    # C1's touched files (app.py/notes.md/config.yaml at the 7d snapshot)
    # are blamed on the first run and served from the cache on the second
    assert first > 0
    assert second == 0


def test_snapshot_subcommand_warms_cache(fixture_env, capsys):
    from yield_audit import cache as cache_mod

    argv = [
        "snapshot",
        "--repo", fixture_env["repo_cwd"],
        "--transcripts-dir", str(fixture_env["transcripts_root"]),
        "--now", NOW,
    ]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "snapshot ok: 3 sessions, 5 commits scanned" in out
    assert "immutable git-fact entries" in out
    assert cache_mod.cache_path(fixture_env["repo_cwd"]).is_file()
