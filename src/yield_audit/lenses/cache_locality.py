"""M5 — cache locality: when did a session pay full price for a warm prefix?

Anthropic's default cache TTL is 5 minutes (refreshed on hit). For each API
call after the first in a session with ``cache_read == 0`` we classify why
the prefix was cold:

- ``compaction`` — a compact boundary sits between the calls; the client
  rebuilt context on purpose. Expected, not scheduling waste.
- ``ttl_expiry`` — more than 5 minutes since the previous call. Expected
  expiry, but a *scheduling* signal: batching the work would have hit.
- ``prefix_break`` — cold despite a short gap. Possible prompt-prefix
  instability; worth investigating.

``wasted_usd`` estimates what non-compaction cold calls would have cost had
their input been a cache read: full-price input vs cache-read price.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..events import Session
from ..pricing import ModelPrice, price_for_model

DEFAULT_TTL = timedelta(minutes=5)

COMPACTED = "compaction"
TTL_EXPIRY = "ttl_expiry"
PREFIX_BREAK = "prefix_break"


@dataclass
class ColdEvent:
    ts: object
    model: str
    gap_seconds: float | None
    class_name: str
    input_tokens: int
    wasted_usd: float


@dataclass
class CacheLocalityResult:
    api_calls: int = 0
    cold_calls: int = 0
    events: list[ColdEvent] = field(default_factory=list)
    by_class: dict[str, int] = field(default_factory=dict)
    wasted_usd: float = 0.0
    hit_rate: float | None = None
    notes: list[str] = field(default_factory=list)


def analyze_cache_locality(
    session: Session,
    table: dict,
    fallback: ModelPrice,
    *,
    ttl: timedelta = DEFAULT_TTL,
) -> CacheLocalityResult:
    result = CacheLocalityResult()
    calls = sorted(session.api_calls, key=lambda c: c.ts)
    result.api_calls = len(calls)
    if not calls:
        return result

    total_read = sum(c.cache_read_tokens for c in calls)
    total_base = sum(c.input_tokens + c.cache_read_tokens + c.cache_write_tokens for c in calls)
    result.hit_rate = (total_read / total_base) if total_base else None

    boundaries = sorted(session.compact_boundaries)
    for index, call in enumerate(calls):
        if index == 0:
            continue  # the first call of a session is legitimately cold
        if call.cache_read_tokens > 0:
            continue
        prev = calls[index - 1]
        gap = (call.ts - prev.ts).total_seconds()
        compacted = any(prev.ts < b <= call.ts for b in boundaries)
        if compacted:
            class_name = COMPACTED
        elif gap > ttl.total_seconds():
            class_name = TTL_EXPIRY
        else:
            class_name = PREFIX_BREAK
        result.cold_calls += 1
        result.by_class[class_name] = result.by_class.get(class_name, 0) + 1

        wasted = 0.0
        if class_name != COMPACTED:
            price = price_for_model(table, fallback, call.model)
            cold_tokens = call.input_tokens + call.cache_write_tokens
            wasted = cold_tokens * max(price.input - price.cache_read, 0.0) / 1_000_000
            result.wasted_usd += wasted
        result.events.append(
            ColdEvent(
                ts=call.ts,
                model=call.model,
                gap_seconds=gap,
                class_name=class_name,
                input_tokens=call.input_tokens + call.cache_write_tokens,
                wasted_usd=wasted,
            )
        )

    result.notes.append(
        "cold classification: compaction boundary = expected; gap > TTL = scheduling signal; short-gap cold = possible prefix break"
    )
    return result
