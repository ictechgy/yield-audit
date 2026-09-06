# Contributing to yield-audit

Thanks for helping measure what AI-generated code actually leaves behind.
Two contribution paths dominate: **new transcript adapters** and **new
lenses**. Both have strict contracts — this project's entire value is
that its numbers are trustworthy and reproducible.

For AI-agent contributors: [AGENTS.md](AGENTS.md) (root, `lenses/`,
`tests/`) takes precedence over this file.

## Ground rules (non-negotiable)

- **Measurement only, no savings claims.** No intervention features
  outside the v1.x evidence gates.
- **Every metric carries a `measurement` label** — `observed`,
  `estimate`, or `proxy`. Unlabeled numbers are bugs.
- **Zero runtime dependencies** (stdlib + git CLI) and **zero network
  calls**. Optional extras (like `[perfetto]`) must defer their imports
  and keep the base install stdlib-only.
- **Deterministic**: no wall clocks in lenses (`now` is a parameter),
  sorted iteration for everything that reaches a report.
- **Local and read-only**: never write into audited repositories; caches
  live under `~/.cache/yield-audit` and are content-addressed.
- `pytest` and `ruff check .` must pass; new metrics need lens unit
  tests **and** end-to-end golden assertions (see `tests/AGENTS.md`).

## Adding a transcript adapter (new vendor)

The pipeline is vendor-neutral: every lens consumes
`events.Session`. A vendor adapter turns local JSONL logs into that
model. Look at `src/yield_audit/transcripts/codex.py` as the reference.

1. **Ground the schema first.** Collect real transcript files from the
   vendor's CLI (yours, or attached to an issue with sensitive values
   stripped) and record the actual key structure — record types,
   payload shapes, tool names, where usage/token counts live, how
   errors are flagged. Do not write an adapter from screenshots or
   memory; synthetic-only adapters ship as "unverified" and rot.
2. **Subclass `TranscriptAdapter`** (`transcripts/base.py`):
   - `name` — vendor key used by `--agent` and the `"vendor:<id>"`
     session namespace;
   - `default_root()` — where the CLI stores transcripts locally;
   - `iter_files()` — override only if the vendor's layout allows
     pruning (e.g. Codex's `YYYY/MM/DD` day-directories);
   - `handle_record()` — parse one decoded JSON record; return `False`
     to stop reading the file when you can prove the rest is another
     project's (Codex does this at `session_meta.cwd`).
3. **Normalize tool names** to the canonical set lenses understand:
   shell execution → `Bash` with `input.command`, file edits → append
   to `Session.edited_files`, `git commit` detection via
   `note_command()`.
4. **Parse defensively**: unknown record types, missing keys, malformed
   JSON, and hostile values (bare `Infinity`!) must degrade to skips,
   never crashes.
5. **Register** the instance in `ADAPTERS` and `DEFAULT_AGENTS`
   (`transcripts/__init__.py`), then add fixtures + tests:
   - synthetic records mirroring the real schema in `tests/conftest.py`
     (docstring documents the grounding date),
   - adapter tests in `tests/test_transcripts_<vendor>.py`,
   - update `tests/AGENTS.md` and both READMEs.

## Adding a lens

Read [src/yield_audit/lenses/AGENTS.md](src/yield_audit/lenses/AGENTS.md)
first. Summary: pure function over `events.Session`/git facts, dataclass
result, guarded division, attribution-share weighting, pending horizons
counted honestly, thresholds as named constants with docstring
rationale. Network-dependent lenses follow the operator-file pattern
(M13's `--ci-runs`): the user exports, yield-audit reads a file.

## Release checklist (maintainers)

1. Version bump in **both** `src/yield_audit/__init__.py` and
   `pyproject.toml`; Keep-a-Changelog entry.
2. `pytest && ruff check .` green; push; CI matrix (3 OS × 3.10/3.12/3.14)
   green.
3. `git tag vX.Y.Z && git push --tags` — `.github/workflows/pypi.yml`
   publishes to PyPI via trusted publishing (no tokens).
