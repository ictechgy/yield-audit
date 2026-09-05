"""M1 — output survival analysis (blame-based, type-split).

For an attributed commit C and each file F it touched, we ask git at a
*snapshot* taken horizon days later: of the lines C added to F, how many are
still exactly as C left them (``git blame`` attributes them to C)? Lines a
later commit rewrote, or files later deleted, count as not survived. Renames
and copies are *not* followed in v0.1 — a renamed file counts as deleted, a
documented limitation.

Commits whose horizon has not yet elapsed are ``pending``: excluded from the
headline rate, counted honestly in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .. import gitdata
from ..attribute import AttributionResult
from ..gitdata import CommitInfo

SOURCE = "source"
TEST = "test"
DOCS = "docs"
CONFIG = "config"
KINDS = (SOURCE, TEST, DOCS, CONFIG)

_TEST_NAMES = ("test_", "_test.", "_test.py", ".spec.", ".test.")
_DOCS_DIRS = ("docs/", "doc/", "changelog", "news/")
_CONFIG_EXTS = (".yml", ".yaml", ".toml", ".json", ".ini", ".cfg", ".lock", ".xml", ".properties")


def classify_path(path: str) -> str:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if any(marker in name for marker in _TEST_NAMES) or "/tests/" in lowered or "/__tests__/" in lowered:
        return TEST
    if lowered.endswith((".md", ".rst", ".txt")) or any(marker in lowered for marker in _DOCS_DIRS):
        return DOCS
    if lowered.endswith(_CONFIG_EXTS):
        return CONFIG
    return SOURCE


@dataclass
class SurvivalUnit:
    session_id: str
    commit_sha: str
    path: str
    kind: str
    added: int
    share: float = 1.0  # attribution share; contested commits sum to 1.0 across sessions
    survived: dict[int, int] = field(default_factory=dict)  # horizon days -> survived lines
    deleted: dict[int, bool] = field(default_factory=dict)  # horizon days -> file deleted at snapshot
    pending_horizons: list[int] = field(default_factory=list)

    @property
    def attributed_added(self) -> float:
        """Lines weighted by attribution share — contested commits split cleanly."""
        return self.added * self.share


@dataclass
class SurvivalResult:
    horizon: int
    units: list[SurvivalUnit]
    pending_count: int  # unique (commit, path) units pending at the headline horizon
    overall: float | None
    overall_added: float  # attribution-share weighted, hence float
    overall_survived: float
    by_kind: dict[str, dict]  # kind -> {added, survived, rate}
    sessions: dict[str, dict]  # session_id -> {added, survived, rate, pending}
    notes: list[str]


def analyze_survival(
    repo: str,
    attributions: AttributionResult,
    commits_by_sha: dict[str, CommitInfo],
    *,
    now: datetime,
    horizons: tuple[int, ...] = (7, 30),
    headline_horizon: int = 7,
    blame_cache: dict | None = None,
) -> SurvivalResult:
    cache = blame_cache if blame_cache is not None else {}
    notes: list[str] = []
    units: list[SurvivalUnit] = []

    for pair in attributions.pairs:
        commit = commits_by_sha.get(pair.commit_sha)
        if commit is None:  # defensive: attribution always comes from the same commit list
            continue
        for path, added in sorted(commit.files.items()):
            if added <= 0:
                continue
            unit = SurvivalUnit(
                session_id=pair.session_id,
                commit_sha=pair.commit_sha,
                path=path,
                kind=classify_path(path),
                added=added,
                share=pair.share,
            )
            for horizon in horizons:
                target = commit.date + timedelta(days=horizon)
                if target > now:
                    unit.pending_horizons.append(horizon)
                    continue
                ref = _snapshot(repo, target, cache)
                deleted = path not in _tree(repo, ref, cache)
                unit.deleted[horizon] = deleted
                if deleted:
                    unit.survived[horizon] = 0
                    continue
                key = (ref, path)
                if key not in cache:
                    cache[key] = gitdata.blame_sha_counts(repo, ref, path)
                unit.survived[horizon] = cache[key].get(commit.sha, 0)
            units.append(unit)

    notes.append("survival semantics: lines still present exactly as the commit left them (git blame); renames/copies not followed in v0.1")

    return _aggregate(units, horizons=horizons, headline_horizon=headline_horizon, notes=notes)


def _snapshot(repo: str, target: datetime, cache: dict) -> str:
    key = ("__snapshot__", target.isoformat())
    if key not in cache:
        cache[key] = gitdata.snapshot_ref(repo, target)
    return cache[key]


def _tree(repo: str, ref: str, cache: dict) -> set[str]:
    """File set at ``ref`` — one ls-tree process replaces N cat-file probes."""
    key = ("__tree__", ref)
    if key not in cache:
        cache[key] = gitdata.tree_files(repo, ref)
    return cache[key]


def _aggregate(
    units: list[SurvivalUnit],
    *,
    horizons: tuple[int, ...],
    headline_horizon: int,
    notes: list[str],
) -> SurvivalResult:
    measured_units = [u for u in units if headline_horizon in u.survived]
    pending_units = [u for u in units if headline_horizon in u.pending_horizons]
    # A contested commit contributes one unit per claimant; the pending count
    # reports physical file-units, so dedupe by (commit, path).
    pending_count = len({(u.commit_sha, u.path) for u in pending_units})

    # All aggregates are attribution-share weighted so a commit contested by
    # two sessions contributes each unit's share exactly once overall.
    total_added = sum(u.attributed_added for u in measured_units)
    total_survived = sum(u.survived[headline_horizon] * u.share for u in measured_units)
    overall = (total_survived / total_added) if total_added else None

    by_kind: dict[str, dict] = {}
    for kind in KINDS:
        kind_units = [u for u in measured_units if u.kind == kind]
        added = sum(u.attributed_added for u in kind_units)
        survived = sum(u.survived[headline_horizon] * u.share for u in kind_units)
        by_kind[kind] = {
            "added": added,
            "survived": survived,
            "rate": (survived / added) if added else None,
        }

    session_ids = sorted({u.session_id for u in units})
    sessions: dict[str, dict] = {}
    for sid in session_ids:
        sid_units = [u for u in measured_units if u.session_id == sid]
        added = sum(u.attributed_added for u in sid_units)
        survived = sum(u.survived[headline_horizon] * u.share for u in sid_units)
        pending = sum(1 for u in units if u.session_id == sid and headline_horizon in u.pending_horizons)
        sessions[sid] = {
            "added": added,
            "survived": survived,
            "rate": (survived / added) if added else None,
            "pending": pending,
        }

    # Both horizons are computed even when the headline only uses one; expose
    # the other as a secondary aggregate when measurable.
    secondary: dict[int, float | None] = {}
    for horizon in horizons:
        if horizon == headline_horizon:
            continue
        h_units = [u for u in units if horizon in u.survived]
        h_added = sum(u.attributed_added for u in h_units)
        h_survived = sum(u.survived[horizon] * u.share for u in h_units)
        secondary[horizon] = (h_survived / h_added) if h_added else None

    return SurvivalResult(
        horizon=headline_horizon,
        units=units,
        pending_count=pending_count,
        overall=overall,
        overall_added=total_added,
        overall_survived=total_survived,
        by_kind=by_kind,
        sessions=sessions,
        notes=notes,
    )
