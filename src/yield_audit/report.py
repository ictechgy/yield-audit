"""Renderers for the report dict: console (plain text) and markdown."""

from __future__ import annotations


def render_console(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    params = report["parameters"]
    inp = report["input"]

    add(f"yield-audit {report['schema_version']}")
    add(f"repo: {params['repo']}")
    add(f"window: last {params['window_days']} days | horizon: {params['headline_horizon_days']}d | generated: {report['generated_at']}")
    add(
        f"input: {inp['sessions']} sessions, {inp['api_calls']} api calls, "
        f"{inp['commits_in_window']} commits ({inp['attributed_commits']} attributed, {inp['unclaimed_commits']} unclaimed)"
    )
    if inp["unknown_models"]:
        add(f"unknown models (conservatively priced): {', '.join(inp['unknown_models'])}")
    add("")

    m1 = report["m1_survival"]
    add("== M1 output survival ==")
    if m1["overall_rate"] is None:
        add(f"no measurable output at the {m1['horizon_days']}d horizon yet (pending units: {m1['pending_units']})")
    else:
        add(f"overall survival: {pct(m1['overall_rate'])} of {m1['added_lines']} added lines (pending units: {m1['pending_units']})")
        for kind, info in m1["by_kind"].items():
            if info["added"]:
                add(f"  {kind:8} {pct(info['rate']):>7}  ({info['survived']}/{info['added']} lines)")
    add("")

    m2 = report["m2_waste"]
    add("== M2 waste cost (bounds) ==")
    add(f"lower ${m2['total_lower_usd']:.2f} — upper ${m2['total_upper_usd']:.2f}")
    add(f"  ({m2['method']})")
    add("")

    m3 = report["m3_retry"]
    add("== M3 retry tax ==")
    if m3["total_tokens"]:
        add(f"tax tokens: {m3['total_tax_tokens']} / {m3['total_tokens']} ({pct(m3['tax_share'])})")
        for chain in m3["failure_chains"][:10]:
            add(f"  [{chain['session']}] {chain['attempts']} attempts, {chain['errors']} errors: {chain['command']}")
    else:
        add("no bash activity observed")
    add("")

    m4 = report["m4_accepted"]
    add("== M4 accepted tasks ==")
    if m4["cost_per_accepted_usd"] is None:
        add("no accepted sessions at this horizon")
    else:
        add(f"cost per accepted session: ${m4['cost_per_accepted_usd']:.2f} ({m4['tokens_per_accepted']} tokens)")
    for status, info in m4["totals"].items():
        add(f"  {status:14} {info['sessions']:>3} sessions  ${info['cost_usd']:.2f}")
    add("")

    m5 = report["m5_cache"]
    add("== M5 cache locality ==")
    rate = m5["mean_session_hit_rate"]
    add(f"cold calls: {m5['cold_calls']} {m5['cold_by_class']} | mean hit rate: {pct(rate)} | wasted vs cached: ${m5['wasted_usd']:.2f}")
    add("")

    m8 = report["m8_verify"]
    add("== M8 verification gap ==")
    add(f"gap rate (sessions committing without a verify command first): {pct(m8['gap_rate'])}")
    for status, info in m8["correlation_with_survival"].items():
        add(f"  {status:24} mean survival {pct(info['mean_survival'])} over {info['sessions']} sessions")
    add("")

    add("-- notes --")
    for note in report["notes"]:
        add(f"  {note}")
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    params = report["parameters"]
    inp = report["input"]

    add("# yield-audit report")
    add("")
    add(f"- repo: `{params['repo']}`")
    add(f"- window: last {params['window_days']} days, headline horizon {params['headline_horizon_days']}d")
    add(f"- generated: {report['generated_at']}")
    add(
        f"- input: {inp['sessions']} sessions, {inp['api_calls']} api calls, "
        f"{inp['commits_in_window']} commits ({inp['attributed_commits']} attributed)"
    )
    if inp["unknown_models"]:
        add(f"- unknown models (conservatively priced): {', '.join(inp['unknown_models'])}")
    add("")

    m1 = report["m1_survival"]
    add("## M1 output survival")
    add("")
    if m1["overall_rate"] is None:
        add(f"No measurable output at the {m1['horizon_days']}d horizon yet (pending units: {m1['pending_units']}).")
    else:
        add(f"**Overall survival: {pct(m1['overall_rate'])}** of {m1['added_lines']} added lines.")
        add("")
        add("| kind | survival | lines |")
        add("|---|---|---|")
        for kind, info in m1["by_kind"].items():
            if info["added"]:
                add(f"| {kind} | {pct(info['rate'])} | {info['survived']}/{info['added']} |")
    add("")

    m2 = report["m2_waste"]
    add("## M2 waste cost (bounds)")
    add("")
    add(f"**lower ${m2['total_lower_usd']:.2f} — upper ${m2['total_upper_usd']:.2f}**")
    add("")
    add(f"`{m2['method']}`")
    add("")

    m3 = report["m3_retry"]
    add("## M3 retry tax")
    add("")
    if m3["total_tokens"]:
        add(f"Tax tokens: {m3['total_tax_tokens']} / {m3['total_tokens']} ({pct(m3['tax_share'])}).")
        add("")
        add("| session | attempts | errors | command |")
        add("|---|---|---|---|")
        for chain in m3["failure_chains"][:20]:
            add(f"| {chain['session']} | {chain['attempts']} | {chain['errors']} | `{chain['command']}` |")
    else:
        add("No bash activity observed.")
    add("")

    m4 = report["m4_accepted"]
    add("## M4 accepted tasks")
    add("")
    if m4["cost_per_accepted_usd"] is None:
        add("No accepted sessions at this horizon.")
    else:
        add(f"**Cost per accepted session: ${m4['cost_per_accepted_usd']:.2f}** ({m4['tokens_per_accepted']} tokens).")
    add("")
    add("| status | sessions | cost |")
    add("|---|---|---|")
    for status, info in m4["totals"].items():
        add(f"| {status} | {info['sessions']} | ${info['cost_usd']:.2f} |")
    add("")

    m5 = report["m5_cache"]
    add("## M5 cache locality")
    add("")
    add(
        f"Cold calls: {m5['cold_calls']} {m5['cold_by_class']} — mean hit rate {pct(m5['mean_session_hit_rate'])}, "
        f"wasted vs cached ${m5['wasted_usd']:.2f}."
    )
    add("")

    m8 = report["m8_verify"]
    add("## M8 verification gap")
    add("")
    add(f"Gap rate: {pct(m8['gap_rate'])}.")
    add("")
    if m8["correlation_with_survival"]:
        add("| status | sessions | mean survival |")
        add("|---|---|---|")
        for status, info in m8["correlation_with_survival"].items():
            add(f"| {status} | {info['sessions']} | {pct(info['mean_survival'])} |")
    add("")

    add("## notes")
    add("")
    for note in report["notes"]:
        add(f"- {note}")
    add("")
    return "\n".join(lines)


def pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
