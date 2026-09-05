"""Per-model price table (USD per MTok) and session cost computation.

Defaults reflect published Anthropic list prices as of 2026-09 (see README's
methodology section). All figures are estimates computed from the *observed*
usage fields in local transcripts — never from a provider billing API.
Unknown models fall back to a conservative high tier and are flagged in the
report so under-pricing is impossible to miss.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float
    cache_write: float

    def cost_usd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
    ) -> float:
        return (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
        ) / 1_000_000


# (input, output, cache_read, cache_write) USD per MTok, standard tier,
# published list prices 2026-09 (Anthropic / OpenAI developers pricing pages).
# OpenAI charges no separate cache-write price; gpt-5.1-codex variants share
# the gpt-5.1 base prices (the codex suffix is not separately listed).
_BUILTIN: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-8": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-7": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-1": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4": (15.0, 75.0, 1.5, 18.75),
    "claude-sonnet-5": (2.0, 10.0, 0.2, 2.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3, 3.75),
    "claude-sonnet-4-5": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5),
    "gpt-5.1-codex": (1.25, 10.0, 0.125, 0.0),
    "gpt-5.1": (1.25, 10.0, 0.125, 0.0),
    "gpt-5.3-codex": (1.75, 14.0, 0.175, 0.0),
    "gpt-5-mini": (0.25, 2.0, 0.025, 0.0),
    "o3": (2.0, 8.0, 0.5, 0.0),
    "o4-mini": (1.1, 4.4, 0.275, 0.0),
}

_FALLBACK_KEY = "__fallback__"


def load_pricing(override_file: str | None = None) -> tuple[dict[str, ModelPrice], ModelPrice, list[str]]:
    """Return (model->price, fallback price, notes). Override file replaces matching keys."""
    table = {
        name: ModelPrice(in_p, out_p, cr, cw)
        for name, (in_p, out_p, cr, cw) in _BUILTIN.items()
    }
    fallback = ModelPrice(5.0, 25.0, 0.5, 6.25)
    notes: list[str] = []
    if override_file:
        data = _read_override(override_file)
        for name, entry in data.items():
            price = _price_from_entry(entry)
            if price is None:
                notes.append(f"pricing override for {name!r} ignored: missing or invalid fields")
                continue
            if name == _FALLBACK_KEY:
                fallback = price
            else:
                table[name] = price
            notes.append(f"pricing override applied: {name}")
    return table, fallback, notes


def _read_override(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("pricing override must be a JSON object")
    models = data.get("models", data)
    if not isinstance(models, dict):
        raise ValueError("pricing override 'models' must be an object")
    return models


def _price_from_entry(entry: Any) -> ModelPrice | None:
    if not isinstance(entry, dict):
        return None
    values = []
    for key in ("input", "output", "cache_read", "cache_write"):
        value = entry.get(key)
        if not isinstance(value, (int, float)) or value < 0:
            return None
        values.append(float(value))
    return ModelPrice(*values)


def price_for_model(table: dict[str, ModelPrice], fallback: ModelPrice, model: str) -> ModelPrice:
    """Exact match first, then longest prefix match (e.g. dated suffixes), else fallback."""
    if model in table:
        return table[model]
    best: tuple[int, ModelPrice] | None = None
    for name, price in table.items():
        if model.startswith(name + "-") or model.startswith(name + "@"):
            if best is None or len(name) > best[0]:
                best = (len(name), price)
    return best[1] if best is not None else fallback
