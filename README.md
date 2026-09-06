# yield-audit

> [한국어 문서](README.ko.md) | English documentation.

**Your CI dashboard says commits are up 40% since the AI rollout. yield-audit
tells you that 22% of that output was reworked within two weeks — and that
the rework rate on AI-marked commits is 1.7x the human rate.**

`yield-audit` is a local, read-only CLI that crosses your AI coding agent's
session transcripts with your git history and reports what actually
*survived*: output survival rate, waste cost bounds, retry tax, cost per
accepted task, cache locality, verification gaps, and AI-vs-human rework
rates. Where usage tools (ccusage et al.) show the *bill*, yield-audit shows
what the tokens left behind.

- **Fully local** — transcripts and git history are read; there is no
  networking code in the package at all.
- **Read-only & deterministic** — nothing is written to your repositories;
  `--now` pins a run for reproducibility.
- **Zero runtime dependencies** — Python ≥ 3.10 stdlib + the `git` CLI.
- **Vendor-neutral** — scans Claude Code (`~/.claude/projects`) and Codex
  CLI (`~/.codex/sessions`, schema verified against real rollouts)
  transcripts; more adapters via a registry ([contributing
  guide](CONTRIBUTING.md)).

## Quick start

```bash
# install (PyPI)
python3 -m pip install yield-audit      # or: uv tool install yield-audit
# one-shot, no install:
uvx yield-audit audit --repo /path/to/your/repo

# audit a repository (auto-scans every installed agent's transcripts)
yield-audit audit --repo /path/to/your/repo

# one vendor only
yield-audit audit --repo . --agent codex

# JSON / markdown reports
yield-audit audit --repo . --format json --details
yield-audit audit --repo . --format markdown > yield-report.md

# AI-transition comparison: two windows split at your rollout date
yield-audit aidd --repo . --split 2026-03-01 --days 90

# session timelines as a Perfetto trace (optional extra)
pip install 'yield-audit[perfetto]'
yield-audit export --perfetto --repo . --out trace.perfetto.json

# pre-warm the blame/tree cache (cron-friendly)
yield-audit snapshot --repo .

# environment check (git, transcript roots, session discovery)
yield-audit doctor --repo /path/to/your/repo

# export the session timeline as a Perfetto trace (optional extra)
python3 -m pip install 'yield-audit[perfetto]'
yield-audit export --perfetto --repo . --out session.perfetto.json
# then drag the file into https://ui.perfetto.dev (parsed locally, never uploaded)
```

Requirements: Python >= 3.10, git. No runtime dependencies. No network calls.
(`export --perfetto` pulls in [agent2perfetto](https://github.com/ictechgy/agent2perfetto)
only when you install the extra.)

## Time windows (and how they interact)

| Flag | Controls | Default |
|---|---|---|
| `--days` | the session **and** commit window; the `probable` cohort exists only where an agent session falls inside it | 30 |
| `--horizons` | M1 survival snapshot horizons (days after each commit) | `7,30` |
| `--rework-days` | M11 rework horizon per commit | 14 |
| `--proximity-hours` | session-to-commit attribution window | 24 |

A repeat audit reuses blame/tree results from `~/.cache/yield-audit`
(content-addressed by git SHA — it can never change an output, only how
fast it arrives; `--no-cache` opts out, `YIELD_AUDIT_CACHE_DIR` relocates).

## Sample output

```text
$ yield-audit audit --repo .
input: 3 sessions, 10 api calls, 4 commits (1 attributed, 3 unclaimed)

== M1 output survival ==
overall survival: 54.2% of 24 added lines (pending units: 0)
  source     50.0%  (5/10 lines)
  test      100.0%  (6/6 lines)
  docs        0.0%  (0/4 lines)
  config     50.0%  (2/4 lines)

== M2 waste cost (bounds) ==
lower $0.00 — upper $0.00
  (session cost x attribution-share-weighted line-share proxy x waste class ...)

== M3 retry tax ==
tax tokens: 240 / 4000 (6.0%)
  [claude:bbbbbbbb] 2 attempts, 2 errors: npm test

== M8 verification gap ==
gap rate (never verified): 0.0% | strict (not verified before last commit): 0.0%

== M11 AI rework ==
reworked within 14d, by cohort (evidence-graded, not verdicts):
  certain    60.0%  (6/10 lines, 0 pending)
  probable   45.8%  (11/24 lines, 0 pending)
  human       0.0%  (0/13 lines, 1 pending)
  AI combined 50.0% vs human 0.0%  (evidence: certain=1, human=2, probable=1)
```

## Lenses (v0.1–v0.3)

| Lens | Question | Nature |
|---|---|---|
| **M1 output survival** | Of the committed lines, how many are still verbatim at the horizon (default 7d)? Split by source/test/docs/config | measured from git history |
| **M2 waste cost** | Money spent on dead output — reported as a lower~upper **bound** (deleted = both bounds; ≥50% lost = upper only) | estimate (bounds) |
| **M3 retry tax** | Token share burned in failure chains (same command repeated after errors) | observed from transcripts |
| **M4 cost per accepted task** | Fully-loaded cost per session whose output survived ≥ 50%; accepted/rejected/pending/no_output | estimate (observed × list price) |
| **M5 cache locality** | Cold calls paying full price from TTL expiry / prefix breaks, and what a cache read would have cost | estimate (observed × list price) |
| **M8 verification gap** | Share of sessions that never ran a verification command before committing, correlated with survival | observed from transcripts |
| **M11 AI rework rate** | How much faster is AI-marked output reworked than human output within the rework horizon (default 14d, `--rework-days`)? Ships with cohort evidence (certain = AI footer / probable = session join / human) — a measurement, not a verdict | measured from git history |
| **M12 settle rate** | Is AI-marked code still there months later? Cohort survival at the settle horizon (default 90d, `--settle-days`) — the complement of M11 at a longer horizon | measured from git history |
| **M14 incident origins** | When fix/revert/rollback commits land, whose lines were they pointing at? Blame-count drops across fix commits, attributed to origin-commit cohorts | proxy |
| **M13 verification-tax transfer** | Did the cost move to CI? Runs and non-passing runs per commit, AI vs human cohorts — from a CI export **you** fetch (`gh run list --json … > ci.json`, then `--ci-runs ci.json`); yield-audit itself never touches the network | observed from provided export |

### Honesty contract

- Every metric carries a `measurement` label: `observed` (read straight from
  transcripts/git) / `estimate` (observed × list price) / `proxy` (a stated
  stand-in, e.g. line-share standing in for per-commit token share).
- Attribution (session↔commit matching) is probabilistic, so every dependent
  number inherits **confidence grades** (`high` = the session ran the commit /
  `medium` = file & time overlap) and contested commits are split and flagged.
- Editing is not waste: <50% line loss is classified as iteration and counted
  in neither bound.
- No savings claims. Measurement only; intervention features stay behind a
  v1.x evidence gate.

## Privacy

- Transcripts and git history are **read only**. Nothing leaves your machine —
  the package contains no networking code.
- Report paths are redacted to basenames by default; absolute and `~/` paths
  inside commands become `<path>` (`--show-paths` to undo). Windows UNC paths
  in free text are a documented gap.
- Every transcript-derived string (session ids included) is stripped of
  ANSI/C0/C1 control characters before it reaches a report, and the finished
  report is deep-sanitized recursively — no format can touch your terminal.
- git subprocesses run with `GIT_*` environment variables removed, so a stray
  `GIT_DIR` in your shell cannot redirect the audit.
- Session ids are truncated to 8 characters in reports.

## Methodology & limits

- **Survival**: `git blame --porcelain` at a snapshot taken horizon-days after
  the commit; lines a later commit rewrote or deleted did not survive.
  Renames/copies are not followed in v0.1 — a renamed file counts as deleted.
- **Token attribution**: transcripts have no per-commit tokens, so session
  cost is split across commits by line share (labeled `proxy`).
- **Commit attribution**: edited-files ∩ commit-files × time proximity
  (default 24h, `--proximity-hours`). Pair programming and manual commits
  grade lower or stay unattributed. Contested commits split evenly and are
  flagged.
- **Scale**: survival/rework blame cost is linear in commits × files; a
  touch-map prefilter skips files no later commit changed, so a
  hundred-commit full-history audit runs in well under a second.
- **Pricing**: published list prices 2026-09 (Anthropic + OpenAI standard
  tier) built into `pricing.py`; override with `--pricing-file`; unknown
  models get a conservative top-tier price and are flagged.
- M8 correlations are observations, not causation. Small session counts
  prove nothing.

## Roadmap

- **v0.2** — ✅ shipped: vendor adapter registry (Claude Code + Codex CLI,
  `--agent`), namespaced session ids. Gemini lands once its schema is
  grounded.
- **v0.3** — ✅ M11 AI rework rate shipped (cohorts certain/probable/human,
  `--rework-days`). Remaining: M12 settle rate (blame snapshots),
  M13/M14 (external CI data).
- **v0.4** — ✅ `aidd` transition report shipped: two windows split at a
  rollout date, AI-vs-human rework cohorts per period (`--split`, `--days`),
  plus a persistent content-addressed cache and Codex transcript pruning.
- **v0.5** — ✅ M12 settle rate (`--settle-days`) and M14 incident-origin
  cohorts shipped, plus `snapshot` (cache pre-warming) and a Perfetto
  export (`export --perfetto`, optional extra).
- **v0.6** — ✅ M13 verification-tax transfer shipped via the
  operator-file pattern (`--ci-runs`, zero network calls — gh fetches,
  yield-audit reads). Codex adapter schema verified against real
  rollouts (`custom_tool_call`/`exec`, status-based errors,
  `write_file` edits, cache-write tokens); adapter contribution guide
  added (CONTRIBUTING.md).
- **v1.x** — intervention layer (retry early-abort hooks, deterministic
  oracle routing) — each behind its own evidence gate.

## Development

```bash
git clone https://github.com/ictechgy/yield-audit && cd yield-audit
python3 -m pip install -e '.[dev]'   # or: uv pip install -e '.[dev]'
pytest                               # tests (fixed-date fixture git repo)
ruff check .                         # lint
```

Contributions: lens logic must stay pure functions, and every new metric
needs a `measurement` label plus a golden test. If you contribute via an AI
agent, [AGENTS.md](AGENTS.md) takes precedence over the general guidance
here. Adding a transcript vendor is one `TranscriptAdapter` subclass plus a
registry entry — see [src/yield_audit/transcripts/](src/yield_audit/transcripts/).

## License

Apache-2.0. Methodological roots: [arXiv:2601.16809](https://arxiv.org/abs/2601.16809)
(survival analysis of AI-generated code) and the fully-loaded-cost-per-success
perspective.
