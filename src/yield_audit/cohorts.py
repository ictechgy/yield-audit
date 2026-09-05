"""Commit cohort labeling: evidence-graded AI involvement (기획서 v0.3).

Every commit lands in exactly one of three cohorts, and the label states the
*matching evidence*, never an authorship verdict (판정 아님 원칙):

- ``certain``  — the commit message carries an AI footer: a
  ``Co-Authored-By: <assistant>`` trailer, a ``Generated with …`` line,
  or the ``🤖`` robot marker used by common templates.
- ``probable`` — no footer, but the commit was claimed by a transcript
  session through the pipeline's usual attribution join (time window +
  shared edited files). Same basis as the heuristic ``medium`` grade:
  proximity is evidence, not proof.
- ``human``    — neither marker is present.

Honesty contract: any percentage reported per cohort ships with the cohort
evidence distribution alongside, so a reader can see how much of the
comparison rests on footers versus heuristics.
"""

from __future__ import annotations

import re

CERTAIN = "certain"
PROBABLE = "probable"
HUMAN = "human"
COHORT_LABELS = (CERTAIN, PROBABLE, HUMAN)

# Specific assistants first so the evidence name is as precise as the
# message allows; the bare 🤖 marker is the weakest signal and comes last.
AI_FOOTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claude", re.compile(r"co-authored-by:[^\n]*claude|generated with claude", re.IGNORECASE)),
    ("codex", re.compile(r"co-authored-by:[^\n]*(?:codex|openai)|generated with (?:openai )?codex", re.IGNORECASE)),
    ("copilot", re.compile(r"co-authored-by:[^\n]*copilot|generated with (?:github )?copilot", re.IGNORECASE)),
    ("gemini", re.compile(r"co-authored-by:[^\n]*gemini|generated with gemini", re.IGNORECASE)),
    ("cursor", re.compile(r"co-authored-by:[^\n]*cursor", re.IGNORECASE)),
    ("robot_marker", re.compile("🤖")),
)

SESSION_JOIN_EVIDENCE = "session_join:time_window+files"
NO_EVIDENCE = "no_ai_evidence"


def footer_evidence(message: str) -> str | None:
    """Name of the AI footer pattern that matched, or ``None``."""
    if not message:
        return None
    for name, pattern in AI_FOOTER_PATTERNS:
        if pattern.search(message):
            return name
    return None


def label_commits(messages: dict[str, str], claimed_shas: set[str]) -> dict[str, tuple[str, str]]:
    """Map sha -> (cohort label, evidence string), deterministically.

    ``claimed_shas`` are the commits the attribution join claimed for some
    agent session (any grade). A footer beats a session match: it travels
    inside the commit itself and does not depend on local transcripts.
    """
    labels: dict[str, tuple[str, str]] = {}
    for sha in sorted(set(messages) | claimed_shas):
        footer = footer_evidence(messages.get(sha, ""))
        if footer is not None:
            labels[sha] = (CERTAIN, f"footer:{footer}")
        elif sha in claimed_shas:
            labels[sha] = (PROBABLE, SESSION_JOIN_EVIDENCE)
        else:
            labels[sha] = (HUMAN, NO_EVIDENCE)
    return labels
