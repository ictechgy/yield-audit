"""M2 — waste cost, reported as honest lower/upper bounds.

Classification per attributed file-unit at the survival horizon:

- ``removed``   — file deleted, or *every* line the commit added is gone.
                  High-confidence waste.
- ``rewritten`` — at least half the added lines are gone. Likely waste.
- ``edited``    — less than half gone. Iteration is normal; not counted as
                  waste in either bound.

Bounds are session cost multiplied by the unit's line-share of the session's
measured attributed output (and by the attribution share when a commit is
contested). The line-share is a *proxy* — tokens cannot be attributed to
individual commits — and every figure below is labeled accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .survival import SurvivalResult, SurvivalUnit

REMOVED = "removed"
REWRITTEN = "rewritten"
EDITED = "edited"

REWRITE_THRESHOLD = 0.5


@dataclass
class WasteBound:
    lower_usd: float
    upper_usd: float
    removed_lines: int
    rewritten_lines: int
    edited_lines: int
    units_by_class: dict[str, int]


def classify_unit(unit: SurvivalUnit, horizon: int) -> str | None:
    if horizon in unit.pending_horizons:
        return None
    survived = unit.survived.get(horizon)
    if survived is None:
        return None
    if unit.deleted.get(horizon) or survived == 0:
        return REMOVED
    lost_ratio = 1.0 - (survived / unit.added) if unit.added else 0.0
    return REWRITTEN if lost_ratio >= REWRITE_THRESHOLD else EDITED


def analyze_waste(
    survival: SurvivalResult,
    session_costs: dict[str, float],
    horizon: int,
) -> dict[str, WasteBound]:
    """Session-id -> bounds. Only sessions with measured output are included."""
    measured_added: dict[str, int] = {}
    for session_id, info in survival.sessions.items():
        if info["added"] > 0:
            measured_added[session_id] = info["added"]

    out: dict[str, WasteBound] = {}
    for session_id in sorted(measured_added):
        total_added = measured_added[session_id]
        cost = session_costs.get(session_id, 0.0)
        lower = upper = 0.0
        removed = rewritten = edited = 0
        class_counts = {REMOVED: 0, REWRITTEN: 0, EDITED: 0}
        for unit in survival.units:
            if unit.session_id != session_id:
                continue
            cls = classify_unit(unit, horizon)
            if cls is None:
                continue
            class_counts[cls] += 1
            share = cost * (unit.added / total_added) if total_added else 0.0
            if cls == REMOVED:
                removed += unit.added
                lower += share
                upper += share
            elif cls == REWRITTEN:
                rewritten += unit.added
                upper += share
            else:
                edited += unit.added
        out[session_id] = WasteBound(
            lower_usd=lower,
            upper_usd=upper,
            removed_lines=removed,
            rewritten_lines=rewritten,
            edited_lines=edited,
            units_by_class=class_counts,
        )
    return out
