"""Renderers for the report dict: console (plain text) and markdown.

Strings in the report dict are already sanitized (control/ANSI stripped,
paths redacted) by the audit pipeline; this module only adds
format-specific escaping (markdown table cells).
"""

from __future__ import annotations

from .redact import markdown_cell


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
    if not inp["sessions"]:
        add("! no agent sessions matched this repo in the window — run `yield-audit doctor --repo <repo>` to check transcript discovery")
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
        if m3.get("chains_truncated"):
            add(f"  … {m3['chains_truncated']} more chains not shown")
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
    add(f"gap rate (never verified): {pct(m8['gap_rate'])} | strict (not verified before last commit): {pct(m8.get('gap_rate_strict'))}")
    for status, info in m8["correlation_with_survival"].items():
        add(f"  {status:24} mean survival {pct(info['mean_survival'])} over {info['sessions']} sessions")
    add("")

    m11 = report["m11_rework"]
    add("== M11 AI rework ==")
    if m11["rework_horizon_days"] <= 0:
        add("disabled (--rework-days 0)")
    else:
        add(f"reworked within {m11['rework_horizon_days']}d, by cohort (evidence-graded, not verdicts):")
        for label in ("certain", "probable", "human"):
            info = m11["cohorts"].get(label)
            if info and info["commits"]:
                add(
                    f"  {label:8} {pct(info['rework_rate']):>7}  "
                    f"({info['reworked_lines']}/{info['added_lines']} lines, {info['pending_commits']} pending)"
                )
        combined = m11["cohorts"].get("ai_combined")
        human = m11["cohorts"].get("human")
        if combined and combined["commits"] and human and human["commits"]:
            add(
                f"  AI combined {pct(combined['rework_rate'])} vs human {pct(human['rework_rate'])}"
                f"  (evidence: {', '.join(f'{k}={v}' for k, v in m11['cohort_evidence'].items())})"
            )
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
    if not inp["sessions"]:
        add("- **no agent sessions matched this repo in the window — run `yield-audit doctor --repo <repo>` to check transcript discovery**")
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
            safe_command = markdown_cell(chain["command"])
            add(f"| {chain['session']} | {chain['attempts']} | {chain['errors']} | `{safe_command}` |")
        if m3.get("chains_truncated"):
            add(f"\n_…{m3['chains_truncated']} more chains not shown._")
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
    add(f"Gap rate (never verified): {pct(m8['gap_rate'])}; strict (not verified before last commit): {pct(m8.get('gap_rate_strict'))}.")
    add("")
    if m8["correlation_with_survival"]:
        add("| status | sessions | mean survival |")
        add("|---|---|---|")
        for status, info in m8["correlation_with_survival"].items():
            add(f"| {status} | {info['sessions']} | {pct(info['mean_survival'])} |")
    add("")

    m11 = report["m11_rework"]
    add("## M11 AI rework")
    add("")
    if m11["rework_horizon_days"] <= 0:
        add("Disabled (`--rework-days 0`).")
    else:
        add(f"Reworked within {m11['rework_horizon_days']}d, by cohort (evidence-graded, not verdicts).")
        add("")
        add("| cohort | rework rate | lines | pending |")
        add("|---|---|---|---|")
        for label in ("certain", "probable", "human", "ai_combined"):
            info = m11["cohorts"].get(label)
            if info and info["commits"]:
                add(
                    f"| {label} | {pct(info['rework_rate'])} | "
                    f"{info['reworked_lines']}/{info['added_lines']} | {info['pending_commits']} |"
                )
        add("")
        evidence = ", ".join(f"{k}={v}" for k, v in m11["cohort_evidence"].items())
        add(f"_Cohort evidence: {evidence}._")
    add("")

    add("## notes")
    add("")
    for note in report["notes"]:
        add(f"- {note}")
    add("")
    return "\n".join(lines)


def render_aidd_console(aidd: dict) -> str:
    lines: list[str] = []
    add = lines.append
    params = aidd["parameters"]
    comp = aidd["comparison"]

    add(f"aidd report {aidd['schema_version']}")
    add(f"repo: {params['repo']}")
    add(
        f"transition split: {params['split']} | lookback {params['period_lookback_days']}d per period "
        f"| rework horizon {params['rework_horizon_days']}d | generated: {aidd['generated_at']}"
    )
    add("")
    for key in ("before", "after"):
        period = aidd["periods"][key]
        add(
            f"[{key}] {period['window_start']} .. {period['window_end'] or 'now'}: "
            f"{period['sessions']} sessions, {period['commits']} commits "
            f"({period['attributed_commits']} attributed), ${period['session_cost_usd']:.2f} agent spend, "
            f"survival {pct(period['survival_rate'])}"
        )
    add("")
    add("== AI rework by cohort (evidence-graded, not verdicts) ==")
    for key in ("before", "after"):
        period = aidd["periods"][key]
        add(f"-- {key} --")
        for label in ("certain", "probable", "human", "ai_combined"):
            info = period["cohorts"].get(label)
            if info and info["commits"]:
                add(
                    f"  {label:12} {pct(info['rework_rate']):>7}  "
                    f"({info['reworked_lines']}/{info['added_lines']} lines, {info['pending_commits']} pending)"
                )
        evidence = ", ".join(f"{k}={v}" for k, v in period["cohort_evidence"].items())
        add(f"  evidence: {evidence}")
    add("")
    add("== comparison ==")
    add(f"AI rework rate: {pct(comp['ai_rework_rate']['before'])} -> {pct(comp['ai_rework_rate']['after'])}")
    add(f"human rework rate: {pct(comp['human_rework_rate']['before'])} -> {pct(comp['human_rework_rate']['after'])}")
    add(f"AI vs human ratio: {comp['ai_vs_human_rework_ratio']['before']} -> {comp['ai_vs_human_rework_ratio']['after']}")
    add("")
    add("-- notes --")
    for note in aidd["notes"]:
        add(f"  {note}")
    return "\n".join(lines)


def render_aidd_markdown(aidd: dict) -> str:
    lines: list[str] = []
    add = lines.append
    params = aidd["parameters"]
    comp = aidd["comparison"]

    add("# yield-audit aidd report")
    add("")
    add(f"- repo: `{params['repo']}`")
    add(f"- transition split: `{params['split']}`, lookback {params['period_lookback_days']}d per period, rework horizon {params['rework_horizon_days']}d")
    add(f"- generated: {aidd['generated_at']}")
    add("")
    add("| period | window | sessions | commits | attributed | agent spend | survival |")
    add("|---|---|---|---|---|---|---|")
    for key in ("before", "after"):
        p = aidd["periods"][key]
        add(
            f"| {key} | {p['window_start']} .. {p['window_end'] or 'now'} | {p['sessions']} | {p['commits']} "
            f"| {p['attributed_commits']} | ${p['session_cost_usd']:.2f} | {pct(p['survival_rate'])} |"
        )
    add("")
    for key in ("before", "after"):
        p = aidd["periods"][key]
        add(f"## {key}: AI rework by cohort")
        add("")
        add("| cohort | rework rate | lines | pending |")
        add("|---|---|---|---|")
        for label in ("certain", "probable", "human", "ai_combined"):
            info = p["cohorts"].get(label)
            if info and info["commits"]:
                add(f"| {label} | {pct(info['rework_rate'])} | {info['reworked_lines']}/{info['added_lines']} | {info['pending_commits']} |")
        evidence = ", ".join(f"{k}={v}" for k, v in p["cohort_evidence"].items())
        add(f"\n_Cohort evidence: {evidence}._")
        add("")
    add("## comparison")
    add("")
    add("| metric | before | after |")
    add("|---|---|---|")
    add(f"| AI rework rate | {pct(comp['ai_rework_rate']['before'])} | {pct(comp['ai_rework_rate']['after'])} |")
    add(f"| human rework rate | {pct(comp['human_rework_rate']['before'])} | {pct(comp['human_rework_rate']['after'])} |")
    ratio = comp["ai_vs_human_rework_ratio"]
    add(f"| AI vs human ratio | {ratio['before'] if ratio['before'] is not None else 'n/a'} | {ratio['after'] if ratio['after'] is not None else 'n/a'} |")
    add(f"| survival rate | {pct(comp['survival_rate']['before'])} | {pct(comp['survival_rate']['after'])} |")
    add("")
    add("## notes")
    add("")
    for note in aidd["notes"]:
        add(f"- {note}")
    add("")
    return "\n".join(lines)


def pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
