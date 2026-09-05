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
    removed_lines: float  # attribution-share weighted
    rewritten_lines: float
    edited_lines: float
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
    """Session-id -> bounds. Only sessions with classified units are included.

    Line counts are attribution-share weighted (``attributed_added``), so a
    commit contested by two sessions contributes to each session's bounds in
    proportion to its share. The share denominator is the sum of units
    *classified at the same horizon* — using the headline-horizon aggregate
    instead would let pending-at-headline units push shares above 1.0.
    """
    units_by_session: dict[str, list[SurvivalUnit]] = {}
    for unit in survival.units:
        if classify_unit(unit, horizon) is not None:
            units_by_session.setdefault(unit.session_id, []).append(unit)

    out: dict[str, WasteBound] = {}
    for session_id in sorted(units_by_session):
        session_units = units_by_session[session_id]
        total_added = sum(u.attributed_added for u in session_units)
        if total_added <= 0:
            continue
        cost = session_costs.get(session_id, 0.0)
        lower = upper = 0.0
        removed = rewritten = edited = 0.0
        class_counts = {REMOVED: 0, REWRITTEN: 0, EDITED: 0}
        for unit in session_units:
            cls = classify_unit(unit, horizon)
            class_counts[cls] += 1
            effective = unit.attributed_added
            share = cost * (effective / total_added)
            if cls == REMOVED:
                removed += effective
                lower += share
                upper += share
            elif cls == REWRITTEN:
                rewritten += effective
                upper += share
            else:
                edited += effective
        out[session_id] = WasteBound(
            lower_usd=lower,
            upper_usd=upper,
            removed_lines=removed,
            rewritten_lines=rewritten,
            edited_lines=edited,
            units_by_class=class_counts,
        )
    return out
