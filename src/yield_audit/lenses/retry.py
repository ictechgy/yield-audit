"""M3 — retry tax: tokens spent re-running commands after failures.

Deterministic rule (v0.1): within one session, group Bash invocations by
normalized command. A group where at least one attempt errored is a failure
chain; every attempt beyond the first in that group is a repeat. The tax is
the token share of API calls issued between the chain's first and last
attempt (inclusive), so re-reading context the model needed to retry is
counted too.

Not counted: retries of *successful* commands (legitimate re-runs like watch
loops are common), and different commands that happen to fail similarly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..events import Session


@dataclass
class RetryChain:
    command: str
    attempts: int
    errors: int
    first_ts: object = None
    last_ts: object = None


@dataclass
class RetryResult:
    chains: list[RetryChain] = field(default_factory=list)
    taxed_api_calls: int = 0
    tax_token_share: float | None = None
    tax_tokens: int = 0
    total_tokens: int = 0
    notes: list[str] = field(default_factory=list)


def analyze_retry(session: Session) -> RetryResult:
    sequence = session.bash_sequence()
    result = RetryResult()

    groups: dict[str, list[tuple[object, bool]]] = {}
    order: list[str] = []
    for ts, command, is_error in sequence:
        if command not in groups:
            groups[command] = []
            order.append(command)
        groups[command].append((ts, is_error))

    taxed_intervals: list[tuple[object, object]] = []
    for command in order:
        attempts = groups[command]
        if len(attempts) < 2:
            continue
        errors = sum(1 for _, is_error in attempts if is_error)
        if errors == 0:
            continue
        chain = RetryChain(
            command=command,
            attempts=len(attempts),
            errors=errors,
            first_ts=attempts[0][0],
            last_ts=attempts[-1][0],
        )
        result.chains.append(chain)
        taxed_intervals.append((chain.first_ts, chain.last_ts))
        result.notes.append(
            f"failure chain: {chain.attempts} attempts ({chain.errors} errors) of {command[:60]}"
        )

    if not session.api_calls:
        return result
    result.total_tokens = sum(c.total_input_tokens + c.output_tokens for c in session.api_calls)
    for call in session.api_calls:
        if any(start <= call.ts <= end for start, end in taxed_intervals):
            result.taxed_api_calls += 1
            result.tax_tokens += call.total_input_tokens + call.output_tokens
    if result.total_tokens:
        result.tax_token_share = result.tax_tokens / result.total_tokens
    return result
