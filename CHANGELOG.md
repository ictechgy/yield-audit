# Changelog

All notable changes to yield-audit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
SemVer.

## [0.3.0] - 2026-09-05

M11 AI rework rate — the first ADD-transition lens (기획서-AIDD-전환계량.md
v0.3.0 scope): compares how quickly AI-marked commits get reworked against
human ones, measured locally from git history alone.

### Added
- `cohorts.py`: every commit lands in one evidence-graded cohort —
  `certain` (AI footer in the commit message: `Co-Authored-By: …`,
  `Generated with …`, 🤖), `probable` (no footer but claimed by a
  transcript session via the usual attribution join), or `human`.
  Labels state matching evidence, never authorship verdicts; every
  cohort percentage ships with the evidence distribution.
- `lenses/rework.py` (M11): rework = lines a commit added that are no
  longer present verbatim at the snapshot `--rework-days` (default 14)
  later — the complement of M1's blame survival, applied to every commit
  and aggregated by cohort plus an `ai_combined` view. Pending horizons
  are excluded from rates and counted honestly; `--rework-days 0`
  disables the lens.
- Report block `m11_rework` and `parameters.rework_horizon_days`
  (schema v1, additive); console/markdown renderers gained an M11
  section; `gitdata.commit_messages` streams full commit messages for
  footer evidence.

### Changed
- Fixture repo grew commits C3 (AI-footered, new file) and C4 (human
  rework of it); M1/attribution goldens are unchanged, window counts
  updated (commits_in_window 4, unclaimed 3).

## [0.2.0] - 2026-09-05

Vendor-agnostic transcripts: the Claude-only ingest module became an
adapter registry, and the Codex CLI is scanned alongside Claude Code by
default. Claude-only audits keep identical measured values (regression:
all v0.1.2 golden numbers unchanged).

### Added
- `transcripts/` package with a `TranscriptAdapter` base class (find
  files + parse one JSON record into `events.Session`) and a registry;
  adding a vendor is one subclass plus one registry entry.
- Codex CLI adapter (`~/.codex/sessions` rollout JSONL): session meta /
  turn context / function calls / token counts, with vendor tool names
  normalized to the canonical set (shell execution becomes `Bash`,
  `apply_patch` headers become edited files, exit_code becomes tool
  errors). Compaction boundaries are not present in this format and stay
  empty.
- `--agent {auto,claude,codex}` on `audit` and `doctor` (default `auto`:
  every registered vendor; missing roots are skipped). An explicit
  `--transcripts-dir` applies to all selected vendors — adapters skip
  records that are not their schema, so mixed directories are safe.
- Session ids are namespaced per vendor (`"claude:<id>"`,
  `"codex:<id>"`); report keys keep the prefix. Report `parameters` adds
  `agents_scanned` and `transcripts_roots` (schema v1, additive).

### Changed
- `run_audit`'s `transcripts_root` accepts `None` (each agent's own
  default root) and takes a new `agents` argument; `load_sessions`
  likewise. Ingest internals moved from `transcripts.py` into
  `transcripts/{base,claude,codex}.py` — the public helpers are
  re-exported unchanged.

## [0.1.2] - 2026-09-05

Second review round: close the sanitizer bypasses found in v0.1.1's new
output boundary and harden ingest against hostile transcript values.
Measured values are unchanged (regression-checked against 0.1.1).

### Fixed — security
- Session ids are transcript-controlled and reached report keys/cells raw;
  `_sid` now sanitizes, and `run_audit` deep-sanitizes the finished report
  (strings and dict keys) so no future field can bypass the boundary.
- Path redaction's lookbehind is ASCII-only: a Unicode word (e.g. Hangul)
  before an absolute path no longer suppresses redaction.
- `~/`-relative paths in commands are redacted to `<path>` like absolute
  ones; Windows UNC free-text paths are a documented gap.
- Sanitization now also strips C1 control characters (U+0080-U+009F) and
  CSI sequences with intermediate bytes.

### Fixed — robustness
- Bare `Infinity` token counts (accepted by Python's json parser) crashed
  the run with OverflowError; non-finite values now read as 0.
- The Windows munged-directory name is separator/colon free, so the
  prefilter can no longer resolve to an absolute path and scan the
  repository itself instead of the transcripts.
- Transcript discovery prunes symlink cycles by (device, inode) instead of
  looping forever.
- Blame porcelain parsing ignores tab-prefixed content lines whose first
  token looks like a SHA.

### Changed
- Discovery notes (prefilter vs full walk) are surfaced in report notes
  instead of being invisible.
- Hygiene: pending count dedupes contested units by (commit, path);
  survival result annotations reflect share-weighted floats; waste
  classifies each unit once; dead attribution overlap field removed;
  version strings aligned to 0.1.2.

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
