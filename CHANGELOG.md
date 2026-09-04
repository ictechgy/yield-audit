# Changelog

All notable changes to yield-audit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
SemVer.

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
