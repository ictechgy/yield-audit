"""M4 — accepted-task accounting: fully loaded cost per accepted session.

A session is ``accepted`` when it produced attributed commits whose measured
survival rate at the horizon is >= 0.5 (more than half of what it wrote is
still there). Sessions with attributed commits too young to measure are
``pending``; sessions with no attributed commits are ``no_output`` (research
or chat work — they spend without producing committed output, reported
separately rather than judged).
"""

from __future__ import annotations

from dataclasses import dataclass, field

ACCEPT_THRESHOLD = 0.5

ACCEPTED = "accepted"
REJECTED = "rejected"
PENDING = "pending_horizon"
NO_OUTPUT = "no_output"


@dataclass
class AcceptedResult:
    sessions: dict[str, dict] = field(default_factory=dict)
    cost_per_accepted_usd: float | None = None
    tokens_per_accepted: int | None = None
    totals: dict[str, dict] = field(default_factory=dict)


def analyze_accepted(
    session_costs: dict[str, dict],    # session_id -> {cost_usd, total_tokens}
    survival_by_session: dict[str, dict],  # SurvivalResult.sessions: {added, rate, ...}
    committed_ids: set[str],           # sessions with at least one attributed commit
) -> AcceptedResult:
    result = AcceptedResult()
    accepted_ids: list[str] = []

    for session_id in sorted(session_costs):
        cost_info = session_costs[session_id]
        if session_id not in committed_ids:
            status = NO_OUTPUT
        else:
            info = survival_by_session.get(session_id, {})
            measured = info.get("added", 0) > 0
            rate = info.get("rate")
            if not measured or rate is None:
                status = PENDING
            elif rate >= ACCEPT_THRESHOLD:
                status = ACCEPTED
            else:
                status = REJECTED
        result.sessions[session_id] = {
            "status": status,
            "cost_usd": cost_info["cost_usd"],
            "total_tokens": cost_info["total_tokens"],
            "survival_rate": survival_by_session.get(session_id, {}).get("rate"),
        }
        if status == ACCEPTED:
            accepted_ids.append(session_id)

    if accepted_ids:
        accepted_cost = sum(result.sessions[s]["cost_usd"] for s in accepted_ids)
        accepted_tokens = sum(result.sessions[s]["total_tokens"] for s in accepted_ids)
        result.cost_per_accepted_usd = accepted_cost / len(accepted_ids)
        result.tokens_per_accepted = int(accepted_tokens / len(accepted_ids))

    for status in (ACCEPTED, REJECTED, PENDING, NO_OUTPUT):
        members = [v for v in result.sessions.values() if v["status"] == status]
        result.totals[status] = {
            "sessions": len(members),
            "cost_usd": sum(v["cost_usd"] for v in members),
            "total_tokens": sum(v["total_tokens"] for v in members),
        }
    return result
