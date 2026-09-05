# Changelog

All notable changes to yield-audit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
SemVer.

## [0.1.1] - 2026-09-05

Post-release review hardening: correctness, performance, and output-boundary
security. Measured values are unchanged (regression-checked against 0.1.0 on
a real corpus).

### Fixed — correctness
- Attribution: contested commits now split across ALL same-grade claimants
  (previously only exact-overlap ties split; other claimants were silently
  dropped, and a high-grade tie picked one winner unflagged). Every
  ambiguity is flagged.
- Attribution: `shared_files` no longer leaks the last-iterated session's
  file set into other pairs (regression test added).
- Waste bounds: the share denominator is now computed from units classified
  at the *same* horizon, so bounds can never exceed the session cost when
  horizon differs from the headline.
- Verification gap: reports both `gap_rate` (never verified) and
  `gap_rate_strict` (not verified before the last commit).
- Cache locality: a compaction boundary stamped exactly at the previous
  call's timestamp now counts as compaction (tie rule).
- Accepted: sessions with commits but no cost record land in a status
  instead of vanishing from totals.

### Fixed — performance
- Survival: file existence is checked via one `git ls-tree -r` per snapshot
  instead of one `cat-file` per unit×horizon (~10x fewer git processes on
  commit-heavy repos).
- Transcripts: sessions are loaded from the repo's munged project directory
  when present (full-walk fallback preserved) — real-corpus audit went from
  14.1s to 3.8s on a 1.3GB transcript root.
- `git log --numstat` and `git blame` stream line-by-line; blame collapses
  to per-SHA counts instead of retaining full porcelain output.
- Failure chains capped at 200 in the JSON report with a truncation flag.
- Cache locality boundary matching is a two-pointer scan, not O(calls×boundaries).

### Fixed — security
- Output boundary: every transcript-derived string is stripped of ANSI
  escape and control sequences before rendering (terminal injection).
- Markdown: chain commands are escaped for table cells/code spans, so a
  pasted report cannot be broken out of or turned into remote image loads.
- Chain commands have absolute paths redacted to `<path>` by default
  (`--show-paths` restores them), matching the README's redaction promise.
- `parameters.transcripts_root` is home-abbreviated; path redaction handles
  Windows separators.
- Git subprocesses run with `GIT_*` environment variables stripped so a
  stray `GIT_DIR` cannot redirect the audit.

### Fixed — robustness
- Unparseable commit dates skip the commit with a warning note instead of
  crashing the run.
- Float token counts in transcripts (e.g. `4096.0`) truncate instead of
  reading as zero.
- `--days` rejects negative values; stdout/stderr are reconfigured to
  survive non-UTF-8 locales.
- `core.quotePath=false` keeps non-ASCII file paths intact in numstat.

### Changed
- Packaging: `license = "Apache-2.0"` (PEP 639) with `license-files`.
- Transcript discovery follows symlinked directories via `os.walk`.

## [0.1.0] - 2026-09-05

Initial release: outcome accounting for AI coding agents, fully local,
measurement-only.

### Added
- Claude Code transcript adapter (JSONL, schema-defensive; sidechains and
  malformed records skipped).
- M1 output survival rate with git-blame snapshots at configurable horizons
  (default 7d headline, 7/30 measured), split by output kind
  (source/test/docs/config); pending-horizon units reported separately;
  aggregates weighted by attribution share so contested commits count once.
- M2 waste cost bounds (removed = lower+upper, >=50% lost = upper only);
  attribution-share-weighted line-share proxy labeled as such.
- M3 retry tax: failure chains from repeated normalized Bash commands with
  errors; interval-based token attribution.
- M4 accepted-task accounting: cost/tokens per accepted session
  (survival >= 0.5); accepted/rejected/pending/no_output classes.
- M5 cache locality: ttl_expiry / prefix_break / compaction classification
  of cold calls; wasted-vs-cached estimate; compaction excluded by design.
- M8 verification gap rate plus survival correlation table.
- Session-commit attribution with high/medium confidence grades, contested
  commit splitting, and ambiguity flags.
- Pricing table (2026-09 list prices, prefix matching, conservative
  fallback for unknown models) with JSON override file.
- Report formats: console, JSON (`yieldaudit.report.v1`, every block
  labeled observed/estimate/proxy), markdown; path redaction by default.
- Graceful degradation on repositories with no commits yet (empty report,
  no crash).
- CLI: `yield-audit audit`, `yield-audit doctor`; `--now` for reproducible
  runs.
- Tests: deterministic fixture git repository (pinned dates) + synthetic
  transcripts; unit and end-to-end golden assertions.
