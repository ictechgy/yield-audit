"""Pricing table behavior and session cost computation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from yield_audit.costs import session_cost
from yield_audit.events import ApiCall, Session
from yield_audit.pricing import load_pricing, price_for_model


def test_exact_and_prefix_model_matching():
    table, fallback, notes = load_pricing()
    assert not notes
    assert table["claude-sonnet-5"].input == 2.0
    assert price_for_model(table, fallback, "claude-sonnet-5") is table["claude-sonnet-5"]
    # dated variant falls back to prefix match
    assert price_for_model(table, fallback, "claude-sonnet-5-20260701").input == 2.0
    # unknown model -> conservative fallback
    assert price_for_model(table, fallback, "gpt-99").input == fallback.input


def test_fable_cache_read_is_discounted():
    table, _, _ = load_pricing()
    assert table["claude-fable-5-1"].cache_read == 0.25


def test_override_file_replaces_entries(tmp_path):
    override = tmp_path / "prices.json"
    override.write_text(
        json.dumps(
            {
                "models": {
                    "claude-sonnet-5": {"input": 1.0, "output": 4.0, "cache_read": 0.1, "cache_write": 1.25},
                    "my-model": {"input": 0.5, "output": 1.5, "cache_read": 0.05, "cache_write": 0.5},
                }
            }
        ),
        encoding="utf-8",
    )
    table, fallback, notes = load_pricing(str(override))
    assert table["claude-sonnet-5"].input == 1.0
    assert price_for_model(table, fallback, "my-model").output == 1.5
    assert any("override applied" in n for n in notes)


def test_override_with_invalid_entry_is_flagged(tmp_path):
    override = tmp_path / "prices.json"
    override.write_text(json.dumps({"models": {"bad": {"input": "free"}}}), encoding="utf-8")
    _, _, notes = load_pricing(str(override))
    assert any("ignored" in n for n in notes)


def _session_with(api_calls) -> Session:
    return Session(
        session_id="s",
        cwd="/repo",
        transcript_path="x",
        start=api_calls[0].ts,
        end=api_calls[-1].ts,
        api_calls=list(api_calls),
    )


def test_session_cost_sums_per_model_prices():
    table, fallback, _ = load_pricing()
    session = _session_with(
        [
            ApiCall(
                ts=datetime(2026, 8, 1, tzinfo=timezone.utc),
                model="claude-sonnet-5",
                input_tokens=1_000_000,
                output_tokens=100_000,
                cache_read_tokens=1_000_000,
                cache_write_tokens=0,
            )
        ]
    )
    cost = session_cost(session, table, fallback)
    # 1M*2 + 0.1M*10 + 1M*0.2 = 2 + 1 + 0.2
    assert cost.cost_usd == pytest.approx(3.2)
    assert cost.total_input_tokens == 2_000_000


def test_unknown_model_uses_fallback_and_is_flagged():
    table, fallback, _ = load_pricing()
    session = _session_with(
        [
            ApiCall(
                ts=datetime(2026, 8, 1, tzinfo=timezone.utc),
                model="mystery-model",
                input_tokens=1_000_000,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
        ]
    )
    cost = session_cost(session, table, fallback)
    assert cost.cost_usd == pytest.approx(fallback.input)
    assert "mystery-model" in cost.unknown_models
