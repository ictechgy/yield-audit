"""Perfetto export: session timelines as ui.perfetto.dev trace JSON.

``agent2perfetto`` renders agent sessions in the Perfetto/Chrome trace
viewer; this module maps yield-audit's vendor-neutral ``events.Session``
model onto that pipeline. The stable contract is agent2perfetto's
**Agent Trace IR** (``docs/agent-trace-ir.md`` in that project) — the
mapping lives here so the event model can evolve without touching the
renderer, and the renderer without touching the lenses.

``agent2perfetto`` is an optional runtime dependency: the base install of
yield-audit stays stdlib-only, and this import is deferred so everything
else works without it (``pip install 'yield-audit[perfetto]'``).

Mapping (one IR turn per event, no cross-event interpretation):

- ``ApiCall``    → ``model_call`` turn; ``cache_read_tokens`` →
  ``cache_read_input_tokens`` and ``cache_write_tokens`` →
  ``cache_creation_input_tokens``, so the exported ctx_*/spend_* counter
  lanes mean exactly what the lenses consume.
- ``ToolUse``    → ``model_call`` turn carrying one tool call (the event
  model does not bind tool uses to a specific API call).
- ``ToolResult`` → ``tool_turn`` with the error flag; result payloads are
  not retained by the model, so no preview is rendered.
"""

from __future__ import annotations

from . import audit
from .events import Session

INSTALL_HINT = (
    "perfetto export requires the optional extra: "
    "pip install 'yield-audit[perfetto]' (equivalently: pip install agent2perfetto)"
)


def _epoch_us(ts) -> int:
    return int(ts.timestamp() * 1_000_000)


def sessions_to_perfetto(sessions: list[Session]) -> dict:
    """Map normalized Sessions to a Perfetto trace JSON dict (traceEvents …)."""
    try:
        from agent2perfetto.ir import (
            KIND_MODEL_CALL,
            KIND_TOOL_TURN,
            AgentTrace,
            IRSession,
            IRStats,
            IRToolCall,
            IRToolResult,
            IRTurn,
        )
        from agent2perfetto.trace import build_trace_from_ir
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise audit.AuditError(INSTALL_HINT) from exc

    ir_sessions: list[IRSession] = []
    for session in sorted(sessions, key=lambda s: s.session_id):
        ir = IRSession(session_id=session.session_id, cwd=session.cwd)
        items: list[IRTurn] = []
        for call in session.api_calls:
            items.append(
                IRTurn(
                    seq=0,
                    epoch_us=_epoch_us(call.ts),
                    timestamp_raw=call.ts.isoformat(),
                    session_id=session.session_id,
                    kind=KIND_MODEL_CALL,
                    vendor_type="api_call",
                    model=call.model,
                    usage={
                        "input_tokens": call.input_tokens,
                        "output_tokens": call.output_tokens,
                        "cache_read_input_tokens": call.cache_read_tokens,
                        "cache_creation_input_tokens": call.cache_write_tokens,
                    },
                )
            )
        for use in session.tool_uses:
            items.append(
                IRTurn(
                    seq=0,
                    epoch_us=_epoch_us(use.ts),
                    timestamp_raw=use.ts.isoformat(),
                    session_id=session.session_id,
                    kind=KIND_MODEL_CALL,
                    vendor_type="tool_use",
                    tool_calls=[IRToolCall(id=use.id, name=use.name, input=dict(use.input))],
                )
            )
        for result in session.tool_results.values():
            items.append(
                IRTurn(
                    seq=0,
                    epoch_us=_epoch_us(result.ts),
                    timestamp_raw=result.ts.isoformat(),
                    session_id=session.session_id,
                    kind=KIND_TOOL_TURN,
                    vendor_type="tool_result",
                    tool_results=[
                        IRToolResult(
                            tool_use_id=result.tool_use_id,
                            content=None,
                            is_error=result.is_error,
                        )
                    ],
                )
            )
        items.sort(key=lambda t: t.epoch_us)
        for seq, turn in enumerate(items):
            turn.seq = seq
        ir.turns = items
        ir_sessions.append(ir)

    trace_ir = AgentTrace(
        vendor="yield-audit",
        source="yield-audit-export",
        sessions=ir_sessions,
        stats=IRStats(),
    )
    return build_trace_from_ir(trace_ir)
