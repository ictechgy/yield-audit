"""Session cost and token accounting from observed usage fields."""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import Session
from .pricing import ModelPrice, price_for_model


@dataclass
class SessionCost:
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_calls: int = 0
    unknown_models: set[str] = field(default_factory=set)

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def session_cost(
    session: Session,
    table: dict[str, ModelPrice],
    fallback: ModelPrice,
) -> SessionCost:
    """Sum per-call costs using each call's own model price.

    Usage fields are observed values from the transcript, so this is a
    *measurement* of tokens multiplied by a *list-price estimate* — the token
    counts are not a proxy, but the USD figure still is (no billing API).
    """
    total = SessionCost(api_calls=len(session.api_calls))
    for call in session.api_calls:
        price = price_for_model(table, fallback, call.model)
        total.cost_usd += price.cost_usd(
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cache_read_tokens=call.cache_read_tokens,
            cache_write_tokens=call.cache_write_tokens,
        )
        total.input_tokens += call.input_tokens
        total.output_tokens += call.output_tokens
        total.cache_read_tokens += call.cache_read_tokens
        total.cache_write_tokens += call.cache_write_tokens
        if call.model not in table:
            total.unknown_models.add(call.model)
    return total
