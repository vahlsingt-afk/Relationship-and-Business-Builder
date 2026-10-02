#!/usr/bin/env python3
"""
canonical_response_eval.py — regression harness for canonical RB responses.

Checks that an assistant response text contains the required canonical
sections, grounding labels, and dispositions for a given scenario, and
that it does NOT contain banned unsupported coaching phrases.

This is a deterministic, source-free evaluator. It does not call an LLM
or RB APIs. It is meant to be run locally as a fast gate before changes
that affect signal/response shape.

Canonical shape lives in: system/api/CANONICAL_RESPONSE_CONTRACT.md
(system/CANONICAL_RESPONSE_CONTRACT.md is RB 9.5 and superseded — see the
notice at the top of that file).

Scenarios supported:
    no_response_update      — Todd-reported silence / waiting / no-reply updates.
    ri_intake_detected      — Manual transcript / email / LinkedIn / recruiting RI.
    known_artifact_ingest   — Recognized upload/export artifact ingestion.
    passive_ri_summary      — Proposed / blocked / duplicate / unavailable passive RI.
    daily_brief_top_contract — Trust-first resource verification before analysis.
    strategic_memory_record — Durable memory write response.

CLI:
    python3 system/scripts/canonical_response_eval.py --smoke
    python3 system/scripts/canonical_response_eval.py --text path/to/response.txt
    python3 system/scripts/canonical_response_eval.py --text-stdin < response.txt
    python3 system/scripts/canonical_response_eval.py --scenario no_response_update --json --text ...
    python3 system/scripts/canonical_response_eval.py --scenario ri_intake_detected --text ...
    python3 system/scripts/canonical_response_eval.py --scenario known_artifact_ingest --text ...
    python3 system/scripts/canonical_response_eval.py --scenario passive_ri_summary --text ...
    python3 system/scripts/canonical_response_eval.py --scenario daily_brief_top_contract --text ...
    python3 system/scripts/canonical_response_eval.py --scenario strategic_memory_record --text ...

Exit codes:
    0   all checks passed (or --smoke had both fixtures behave as expected)
    1   one or more checks failed
    2   input error (bad args, file not found, unsupported scenario)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, NamedTuple


# ----------------------------------------------------------------------
# Scenario specs
# ----------------------------------------------------------------------

# Required headings for canonical no_response_update responses. Matching is
# case-insensitive and tolerates a trailing colon. The actual rendered
# template lives in system/api/custom_gpt_prompt.md and the doctrine in
# system/OPERATIONALIZATION.md Phase 9C; keep this list aligned with those.
NO_RESPONSE_REQUIRED_SECTIONS = [
    "Observed signals",
    "State changes",
    "Inferences",
    "Recommended actions",
    "Persistence",
]

# Any one of these grounding labels must appear (case-insensitive).
GROUNDING_LABELS = [
    "manual_user_provided",
    "system_detected",
    "inferred",
    "stale_source_limited",
]

# Any one of these dispositions must appear (case-insensitive). Match as a
# word so we don't false-positive on e.g. "monitoring".
DISPOSITIONS = [
    "act_today",
    "monitor",
    "ask_todd",
    "ignore",
]

# Banned coaching/advisor phrases. If any of these appears in the response
# OUTSIDE of a quoted/code context (backticks, straight quotes, curly
# quotes), the check fails. Quoting allows responses to reference a defect
# without tripping the check — e.g. when a brief explicitly calls out that
# it is *not* a "strategic advisor" memo.
BANNED_COACHING_PHRASES = [
    "strategic advisor",
    "market positioning",
    "timing says",
    "being treated as",
    # Broad motivational / career-coach framing. Keep this list small and
    # explicit so the check stays deterministic.
    "lean into your",
    "unlock your",
    "embrace this moment",
    "trust the process",
    "own your narrative",
]

# P-036: phrases RB must never produce unless a canonical write actually
# happened (status=recorded with confirmed mutations.py write).  These are
# overclaim phrases that imply a write occurred when status may be proposed,
# blocked, duplicate, or irrelevant.
#
# These are added globally so they apply to ALL scenario evaluators via
# _find_banned_phrases(text, BANNED_COACHING_PHRASES + P036_TRUST_CLAIM_PHRASES).
# They are intentionally narrow — exact phrases only, case-insensitive — to
# avoid false positives in grounding/proof context where "recorded" may
# appear legitimately as a noun (e.g. "the recorded event").
P036_TRUST_CLAIM_PHRASES = [
    "trust updated",
    "relationship state updated",
    "I've updated your notes on",
    "I have updated your notes on",
    "I've updated your relationship with",
    "I have updated your relationship with",
    "I captured that",
    "I've captured that",
]

# Combined global banned list used by scenarios that opt in to P-036 checks.
ALL_BANNED_PHRASES = BANNED_COACHING_PHRASES + P036_TRUST_CLAIM_PHRASES


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    scenario: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "passed": self.passed,
            "checks": [asdict(c) for c in self.checks],
            "failed_checks": [c.name for c in self.checks if not c.passed],
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

# Captures any text inside paired backticks ( `...` and ```...``` ), straight
# double quotes ("..."), straight single quotes ('...' — careful with
# apostrophes but safe enough for short banned-phrase checks), and curly
# quotes (“...” / ‘...’). Multiline-safe.
_QUOTED_SPAN_RE = re.compile(
    r"```.*?```"
    r"|`[^`]*`"
    r'|"[^"]*"'
    r"|“[^”]*”"
    r"|‘[^’]*’",
    re.DOTALL,
)


def _strip_quoted_spans(text: str) -> str:
    """Return text with quoted/code spans blanked out (replaced with spaces).

    Used so banned-phrase checks ignore content the response explicitly
    quoted as a defect or as a reference, e.g. `strategic advisor`.
    """
    return _QUOTED_SPAN_RE.sub(lambda m: " " * len(m.group(0)), text)


def _contains_section(text: str, heading: str) -> bool:
    """True if `heading` appears as a section label.

    Matches case-insensitively. Allows:
    - Markdown headers (#, ##, ###)
    - Trailing colon with end-of-line:   "RB match:"
    - Inline label:                      "RB match: matched (id)"
    - Heading with qualifier:            "Resource Verification & Freshness Status:"

    The heading must appear at the start of a line (modulo leading whitespace
    and optional markdown #) so we don't false-positive on inline prose.
    """
    pattern = re.compile(
        r"^\s{0,3}(?:#{1,6}\s*)?" + re.escape(heading) + r"[\s:&(]",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(pattern.search(text))


def _contains_any_token(text: str, tokens: Iterable[str]) -> tuple[bool, list[str]]:
    """True if any token appears as a whole word/identifier (case-insensitive).

    Returns (found, matched_tokens).
    """
    matched: list[str] = []
    for tok in tokens:
        # \b doesn't work cleanly around underscores; build a manual boundary
        # that treats word/identifier characters as the run.
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        if pattern.search(text):
            matched.append(tok)
    return (len(matched) > 0, matched)


def _find_banned_phrases(text: str, phrases: Iterable[str]) -> list[str]:
    """Return banned phrases that appear OUTSIDE quoted/code spans."""
    unquoted = _strip_quoted_spans(text)
    hits: list[str] = []
    for phrase in phrases:
        if re.search(re.escape(phrase), unquoted, flags=re.IGNORECASE):
            hits.append(phrase)
    return hits


# ----------------------------------------------------------------------
# Scenario: no_response_update
# ----------------------------------------------------------------------


def evaluate_no_response_update(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    # 1. Required sections.
    for heading in NO_RESPONSE_REQUIRED_SECTIONS:
        present = _contains_section(text, heading)
        checks.append(
            CheckResult(
                name=f"section:{heading}",
                passed=present,
                detail="present" if present else "missing required section heading",
            )
        )

    # 2. At least one grounding label.
    grounded, matched = _contains_any_token(text, GROUNDING_LABELS)
    checks.append(
        CheckResult(
            name="grounding_label",
            passed=grounded,
            detail=(
                f"found: {', '.join(matched)}"
                if grounded
                else f"need one of: {', '.join(GROUNDING_LABELS)}"
            ),
        )
    )

    # 3. At least one disposition.
    dispositioned, matched = _contains_any_token(text, DISPOSITIONS)
    checks.append(
        CheckResult(
            name="disposition",
            passed=dispositioned,
            detail=(
                f"found: {', '.join(matched)}"
                if dispositioned
                else f"need one of: {', '.join(DISPOSITIONS)}"
            ),
        )
    )

    # 4. No banned unsupported coaching phrases (outside quoted spans).
    banned_hits = _find_banned_phrases(text, BANNED_COACHING_PHRASES)
    checks.append(
        CheckResult(
            name="no_banned_coaching_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all appear in quoted/code context)"
                if not banned_hits
                else "unquoted coaching phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="no_response_update", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# Scenario: ri_intake_detected
# ----------------------------------------------------------------------

# Required section headings for a manual RI intake response.
RI_INTAKE_REQUIRED_SECTIONS = [
    "RB match",
    "Signal read",
    "Persistence status",
    "Recommended action",
]

# Required action-state verbs (at least one must appear).
RI_INTAKE_ACTION_VERBS = [
    "RB proposed",
    "RB recorded",
    "RB blocked",
    "RB skipped",
    "RB did not persist",
    "Pending confirmation",
    "not_persisted",
    "proposed_write_pending_confirmation",
    "persisted",
]

# Grounding label required for manual intake (must be manual_user_provided).
RI_INTAKE_REQUIRED_GROUNDING = "manual_user_provided"

# Banned phrases specific to RI intake (in addition to global banned set).
RI_INTAKE_BANNED_PHRASES = [
    "should be marked",
    "would likely",
    "could be updated",
    "being treated as",
]


def evaluate_ri_intake_detected(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    # 1. Required sections.
    for heading in RI_INTAKE_REQUIRED_SECTIONS:
        present = _contains_section(text, heading)
        checks.append(
            CheckResult(
                name=f"section:{heading}",
                passed=present,
                detail="present" if present else "missing required section heading",
            )
        )

    # 2. Grounding must be manual_user_provided.
    grounding_present, _ = _contains_any_token(text, [RI_INTAKE_REQUIRED_GROUNDING])
    checks.append(
        CheckResult(
            name="grounding:manual_user_provided",
            passed=grounding_present,
            detail=(
                "found: manual_user_provided"
                if grounding_present
                else "manual intake must carry grounding: manual_user_provided"
            ),
        )
    )

    # 3. At least one action-state verb or persistence token.
    actioned, matched = _contains_any_token(text, RI_INTAKE_ACTION_VERBS)
    checks.append(
        CheckResult(
            name="action_state_verb",
            passed=actioned,
            detail=(
                f"found: {', '.join(matched)}"
                if actioned
                else f"need one of: {', '.join(RI_INTAKE_ACTION_VERBS)}"
            ),
        )
    )

    # 4. At least one disposition.
    dispositioned, matched = _contains_any_token(text, DISPOSITIONS)
    checks.append(
        CheckResult(
            name="disposition",
            passed=dispositioned,
            detail=(
                f"found: {', '.join(matched)}"
                if dispositioned
                else f"need one of: {', '.join(DISPOSITIONS)}"
            ),
        )
    )

    # 5. No banned unsupported coaching / overclaim phrases.
    all_banned = list(BANNED_COACHING_PHRASES) + RI_INTAKE_BANNED_PHRASES
    banned_hits = _find_banned_phrases(text, all_banned)
    checks.append(
        CheckResult(
            name="no_banned_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all appear in quoted/code context)"
                if not banned_hits
                else "unquoted banned phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    # 6. system_detected must NOT appear as the primary grounding on a manual
    #    intake (a manual paste can never be system_detected at the top level).
    #    We check: if system_detected appears but manual_user_provided does not,
    #    that is a grounding mis-classification.
    system_detected_present, _ = _contains_any_token(text, ["system_detected"])
    if system_detected_present and not grounding_present:
        grounding_misclassified = True
    else:
        grounding_misclassified = False
    checks.append(
        CheckResult(
            name="grounding_not_misclassified_as_system_detected",
            passed=not grounding_misclassified,
            detail=(
                "OK — manual_user_provided present alongside system_detected"
                if not grounding_misclassified
                else "system_detected appears but manual_user_provided is absent — "
                     "a pasted/screenshot input must be manual_user_provided"
            ),
        )
    )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="ri_intake_detected", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# Scenario: known_artifact_ingest
# ----------------------------------------------------------------------

KNOWN_ARTIFACT_REQUIRED_TOKENS = [
    "RI found",
    "LinkedIn export ZIP",
    "Persistence status",
]

KNOWN_ARTIFACT_ACTION_TOKENS = [
    "RB recognized",
    "RB ingested",
    "ingestLinkedInExport",
    "POST /linkedin/ingest",
    "file path",
    "attachment handle",
]

KNOWN_ARTIFACT_BANNED_PHRASES = [
    "What would you like done with it",
    "What would you like to do with it",
    "Examples:",
    "Analyze your network",
    "Generate CRM-ready",
    "Create a defect report",
]


def evaluate_known_artifact_ingest(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    for token in KNOWN_ARTIFACT_REQUIRED_TOKENS:
        present = re.search(re.escape(token), text, flags=re.IGNORECASE) is not None
        checks.append(
            CheckResult(
                name=f"token:{token}",
                passed=present,
                detail="present" if present else "missing required artifact-ingest token",
            )
        )

    actioned, matched = _contains_any_token(text, KNOWN_ARTIFACT_ACTION_TOKENS)
    checks.append(
        CheckResult(
            name="known_artifact_action",
            passed=actioned,
            detail=(
                f"found: {', '.join(matched)}"
                if actioned
                else "must either ingest via LinkedIn endpoint or ask only for file path/attachment handle"
            ),
        )
    )

    banned_hits = _find_banned_phrases(text, KNOWN_ARTIFACT_BANNED_PHRASES)
    checks.append(
        CheckResult(
            name="no_generic_upload_menu",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found"
                if not banned_hits
                else "generic upload-menu phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="known_artifact_ingest", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# Scenario: passive_ri_summary
# ----------------------------------------------------------------------

# Required elements in a passive RI summary response (flexible heading matching).
PASSIVE_RI_REQUIRED_TOKENS = [
    # At least one of these count/action-state tokens must be present.
    "RB proposed",
    "RB recorded",
    "RB blocked",
    "RB skipped",
    "RB did not persist",
    "candidates_seen",
    "events_written",
    "proposed_write_pending_confirmation",
    "not_persisted",
]

# Passive RI must carry a source label.
PASSIVE_RI_GROUNDING_TOKENS = [
    "system_detected",
    "passive_signal",
    "relationship_signals",
    "linkedin_messaging",
]

# Banned overclaim: passive events must not be presented as completed.
PASSIVE_RI_BANNED_PHRASES = [
    "RB recorded.*passive",   # regex-style — use simple substring check below
    "should be marked",
    "would likely",
]


def evaluate_passive_ri_summary(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    # 1. At least one action-state verb or count token present.
    actioned, matched = _contains_any_token(text, PASSIVE_RI_REQUIRED_TOKENS)
    checks.append(
        CheckResult(
            name="action_state_or_count_token",
            passed=actioned,
            detail=(
                f"found: {', '.join(matched)}"
                if actioned
                else f"need one of: {', '.join(PASSIVE_RI_REQUIRED_TOKENS)}"
            ),
        )
    )

    # 2. Source grounding label present.
    grounded, matched = _contains_any_token(text, PASSIVE_RI_GROUNDING_TOKENS)
    checks.append(
        CheckResult(
            name="source_grounding_label",
            passed=grounded,
            detail=(
                f"found: {', '.join(matched)}"
                if grounded
                else f"need one of: {', '.join(PASSIVE_RI_GROUNDING_TOKENS)}"
            ),
        )
    )

    # 3. Persistence status present.
    persistence_tokens = [
        "not_persisted",
        "proposed_write_pending_confirmation",
        "persisted",
        "Pending confirmation",
    ]
    has_persistence, matched = _contains_any_token(text, persistence_tokens)
    checks.append(
        CheckResult(
            name="persistence_status",
            passed=has_persistence,
            detail=(
                f"found: {', '.join(matched)}"
                if has_persistence
                else f"need one of: {', '.join(persistence_tokens)}"
            ),
        )
    )

    # 4. No banned overclaim phrases.
    simple_banned = ["should be marked", "would likely", "could be updated"]
    banned_hits = _find_banned_phrases(text, simple_banned)
    checks.append(
        CheckResult(
            name="no_banned_overclaim_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all appear in quoted/code context)"
                if not banned_hits
                else "unquoted banned phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    # 5. If stale_source_limited appears, "RB could not prove quiet" or an
    #    explicit stale-source acknowledgement must also appear.
    stale_present, _ = _contains_any_token(text, ["stale_source_limited"])
    if stale_present:
        stale_ack_tokens = [
            "RB could not prove",
            "stale",
            "under_instrumented",
            "could not access",
        ]
        ack_present, _ = _contains_any_token(text, stale_ack_tokens)
        checks.append(
            CheckResult(
                name="stale_source_acknowledged",
                passed=ack_present,
                detail=(
                    "stale_source_limited acknowledged"
                    if ack_present
                    else "stale_source_limited present but no stale-source acknowledgement found"
                ),
            )
        )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="passive_ri_summary", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# Scenario: daily_brief_top_contract
#
# RB-DEFECT-2026-07-07: this scenario used to check against the RB 9.5
# contract (system/CANONICAL_RESPONSE_CONTRACT.md, pre-Part1/2-split) —
# requiring visible grounding/freshness/disposition labels and "Resource
# Verification"/"What to Ignore" sections. RB 10.6's rewrite of the current
# authoritative contract (system/api/CANONICAL_RESPONSE_CONTRACT.md)
# explicitly REVERSED that: `grounding`, `freshness`, `confidence`,
# `disposition` are API payload fields that must NEVER appear as visible
# text labels in rendered output ("Metadata suppression invariant"). This
# scenario was flagging correct, contract-compliant Daily Brief output as
# non-compliant. Rewritten to check the CURRENT rules.
# ----------------------------------------------------------------------

# Visible metadata labels are now PROHIBITED (they must stay API-only fields).
_VISIBLE_METADATA_LABEL_RE = re.compile(
    r"\b(Grounding|Freshness|Disposition|Confidence)\s*:\s*\S+",
    re.IGNORECASE,
)

# The mandatory close of the Daily Brief (Part 2) per DAILY_BRIEF_CANONICAL.md.
DAILY_BRIEF_REQUIRED_SECTIONS = [
    "CoS Bottom Line",
]

# Stale-source contract: if a source is flagged stale, must not claim quiet.
DAILY_BRIEF_QUIET_CLAIMS = [
    "quiet day",
    "no new signals",
    "no activity",
    "nothing to report",
]

DAILY_BRIEF_BANNED_PHRASES = [
    "should be marked",
    "would likely",
    "could be updated",
]


def evaluate_daily_brief_top_contract(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    # 1. Required sections present.
    for heading in DAILY_BRIEF_REQUIRED_SECTIONS:
        present = _contains_section(text, heading)
        checks.append(
            CheckResult(
                name=f"section:{heading}",
                passed=present,
                detail="present" if present else "missing required section heading",
            )
        )

    # 2. Metadata suppression invariant: no visible grounding/freshness/
    # disposition/confidence labels as rendered text (outside quoted/code
    # context, which is allowed for e.g. documentation examples).
    unquoted = _strip_quoted_spans(text)
    label_hits = _VISIBLE_METADATA_LABEL_RE.findall(unquoted)
    checks.append(
        CheckResult(
            name="no_visible_metadata_labels",
            passed=(len(label_hits) == 0),
            detail=(
                "OK — no visible Grounding/Freshness/Disposition/Confidence labels"
                if not label_hits
                else "found visible metadata labels (must be API-only): " + ", ".join(sorted(set(label_hits)))
            ),
        )
    )

    # 3. Stale-source contract: if stale present, no confident quiet claim.
    stale_present, _ = _contains_any_token(text, ["stale"])
    if stale_present:
        quiet_hit = any(
            re.search(re.escape(q), unquoted, flags=re.IGNORECASE)
            for q in DAILY_BRIEF_QUIET_CLAIMS
        )
        checks.append(
            CheckResult(
                name="no_quiet_claim_when_stale",
                passed=not quiet_hit,
                detail=(
                    "OK — no confident quiet claim found despite stale source"
                    if not quiet_hit
                    else "stale source present but response makes a confident quiet claim"
                ),
            )
        )

    # 4. No banned overclaim phrases.
    banned_hits = _find_banned_phrases(text, DAILY_BRIEF_BANNED_PHRASES)
    checks.append(
        CheckResult(
            name="no_banned_overclaim_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all appear in quoted/code context)"
                if not banned_hits
                else "unquoted banned phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="daily_brief_top_contract", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# Scenario: strategic_memory_record
# ----------------------------------------------------------------------

# Required tokens for a strategic memory record response.
STRATEGIC_MEMORY_REQUIRED_TOKENS = [
    # At least one of these action verbs must be present.
    "RB recorded",
    "RB updated",
    "recorded_new",
    "updated_existing",
]

# Persistence tokens.
STRATEGIC_MEMORY_PERSISTENCE_TOKENS = [
    "persisted",
    "stored",
    "Stored in",
    "strategic_memory.json",
]

# Grounding for strategic memory must be manual_user_provided.
STRATEGIC_MEMORY_REQUIRED_GROUNDING = "manual_user_provided"

# Banned phrases for strategic memory.
STRATEGIC_MEMORY_BANNED_PHRASES = [
    "This should be remembered",
    "should be marked",
    "would likely",
    "not_recorded_no_trigger",  # only banned if trigger was detected (we check conditionally)
]


def evaluate_strategic_memory_record(text: str) -> EvalReport:
    checks: list[CheckResult] = []

    # 1. Action-state verb present.
    actioned, matched = _contains_any_token(text, STRATEGIC_MEMORY_REQUIRED_TOKENS)
    checks.append(
        CheckResult(
            name="action_state_verb",
            passed=actioned,
            detail=(
                f"found: {', '.join(matched)}"
                if actioned
                else f"need one of: {', '.join(STRATEGIC_MEMORY_REQUIRED_TOKENS)}"
            ),
        )
    )

    # 2. Persistence / storage reference present.
    stored, matched = _contains_any_token(text, STRATEGIC_MEMORY_PERSISTENCE_TOKENS)
    checks.append(
        CheckResult(
            name="persistence_or_storage_ref",
            passed=stored,
            detail=(
                f"found: {', '.join(matched)}"
                if stored
                else f"need one of: {', '.join(STRATEGIC_MEMORY_PERSISTENCE_TOKENS)}"
            ),
        )
    )

    # 3. Grounding label (manual_user_provided expected).
    grounding_present, _ = _contains_any_token(text, [STRATEGIC_MEMORY_REQUIRED_GROUNDING])
    # Also accept generic grounding labels for flexibility.
    any_grounding, _ = _contains_any_token(text, GROUNDING_LABELS)
    checks.append(
        CheckResult(
            name="grounding_label",
            passed=any_grounding,
            detail=(
                "found grounding label"
                if any_grounding
                else f"need one of: {', '.join(GROUNDING_LABELS)}"
            ),
        )
    )

    # 4. No "This should be remembered" or similar non-action phrases.
    banned_hits = _find_banned_phrases(text, STRATEGIC_MEMORY_BANNED_PHRASES[:3])
    checks.append(
        CheckResult(
            name="no_non_action_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all appear in quoted/code context)"
                if not banned_hits
                else "unquoted non-action phrases: " + ", ".join(banned_hits)
            ),
        )
    )

    # 5. Future-use or reuse requirements reference.
    future_tokens = [
        "future_use",
        "Future-use",
        "Future use",
        "reuse",
        "RB should reuse",
        "when evaluating",
    ]
    has_future, matched = _contains_any_token(text, future_tokens)
    checks.append(
        CheckResult(
            name="future_use_requirements",
            passed=has_future,
            detail=(
                f"found: {', '.join(matched)}"
                if has_future
                else "future-use requirements block is missing"
            ),
        )
    )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="strategic_memory_record", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# P-036: RI assessment and trust display scenario
# ----------------------------------------------------------------------
# Tests that a response about relationship intelligence correctly uses
# P-036 language and does NOT overclaim a write that did not happen.
#
# Designed to catch:
#   1. Banned trust-claim phrases ("trust updated", etc.)
#   2. Responses that say "recorded" for a proposed-only event
#   3. Missing uncertainty language when status is blocked/stale
#   4. Missing caveat when source is unavailable
#
# Usage note: the scenario is parameterised via optional kwargs because
# the grading depends on the RI status in context.  Pass `ri_status` as
# one of: "proposed", "blocked", "duplicate", "irrelevant", "unavailable",
# "recorded".

_P036_PROPOSED_TOKENS = [
    "would propose", "I propose", "propose recording",
    "proposed", "pending confirmation", "confirm to apply",
    "awaiting confirmation", "review-first",
]
_P036_BLOCKED_TOKENS = [
    "blocked", "did not record", "could not record", "insufficient",
    "confidence was", "stale", "I assessed", "below the threshold",
    "not recorded because", "not enough evidence",
]
_P036_UNAVAILABLE_TOKENS = [
    "unavailable", "could not access", "not been fetched",
    "stale", "incomplete", "hasn't been fetched", "cannot assess",
]
_P036_RECORDED_TOKENS = [
    "recorded", "updated", "wrote", "mutation applied",
    "last_touch is now", "now updated",
]


def evaluate_ri_trust_display(
    text: str,
    *,
    ri_status: str = "proposed",
) -> EvalReport:
    """Evaluate a response for P-036 RI assessment and trust display compliance.

    Args:
        text: The assistant response text to evaluate.
        ri_status: The actual RI assessment status that produced the response.
            One of: proposed, blocked, duplicate, irrelevant, unavailable, recorded.
            Defaults to 'proposed' (the most common mutation-pending state).
    """
    checks: list[CheckResult] = []

    # 1. No banned trust-claim or coaching phrases (P-036 + global).
    banned_hits = _find_banned_phrases(text, ALL_BANNED_PHRASES)
    checks.append(
        CheckResult(
            name="no_banned_trust_claim_phrases",
            passed=(len(banned_hits) == 0),
            detail=(
                "none found (or all in quoted/code context)"
                if not banned_hits
                else "banned phrases found: " + ", ".join(banned_hits)
            ),
        )
    )

    # 2. Status-appropriate language.
    if ri_status == "proposed":
        has_proposed, matched = _contains_any_token(text, _P036_PROPOSED_TOKENS)
        checks.append(
            CheckResult(
                name="proposed_status_language",
                passed=has_proposed,
                detail=(
                    f"found: {', '.join(matched)}"
                    if has_proposed
                    else (
                        "response should include proposal/confirmation language "
                        f"(e.g. {_P036_PROPOSED_TOKENS[:3]!r}) for status=proposed"
                    )
                ),
            )
        )
        # Must NOT claim it was recorded.
        has_recorded, matched = _contains_any_token(text, ["I recorded", "was recorded", "has been recorded"])
        checks.append(
            CheckResult(
                name="no_false_recorded_claim",
                passed=not has_recorded,
                detail=(
                    "no false 'recorded' claim found"
                    if not has_recorded
                    else (
                        f"response claims 'recorded' ({', '.join(matched)}) "
                        "but ri_status=proposed — write has not been confirmed"
                    )
                ),
            )
        )

    elif ri_status == "blocked":
        has_blocked, matched = _contains_any_token(text, _P036_BLOCKED_TOKENS)
        checks.append(
            CheckResult(
                name="blocked_status_language",
                passed=has_blocked,
                detail=(
                    f"found: {', '.join(matched)}"
                    if has_blocked
                    else (
                        "response should explain why RI was not recorded "
                        f"(e.g. {_P036_BLOCKED_TOKENS[:3]!r}) for status=blocked"
                    )
                ),
            )
        )

    elif ri_status == "unavailable":
        has_caveat, matched = _contains_any_token(text, _P036_UNAVAILABLE_TOKENS)
        checks.append(
            CheckResult(
                name="unavailable_source_caveat",
                passed=has_caveat,
                detail=(
                    f"found: {', '.join(matched)}"
                    if has_caveat
                    else (
                        "response should include a caveat about the unavailable source "
                        f"(e.g. {_P036_UNAVAILABLE_TOKENS[:3]!r})"
                    )
                ),
            )
        )

    elif ri_status == "recorded":
        has_recorded, matched = _contains_any_token(text, _P036_RECORDED_TOKENS)
        checks.append(
            CheckResult(
                name="recorded_status_language",
                passed=has_recorded,
                detail=(
                    f"found: {', '.join(matched)}"
                    if has_recorded
                    else (
                        "status=recorded but response lacks confirmation language "
                        f"(e.g. {_P036_RECORDED_TOKENS[:3]!r})"
                    )
                ),
            )
        )

    elif ri_status in ("duplicate", "irrelevant"):
        # These statuses should generally suppress trust language; no
        # required tokens, but the banned list still applies (check 1 above).
        checks.append(
            CheckResult(
                name=f"{ri_status}_suppression_ok",
                passed=True,
                detail=(
                    f"ri_status={ri_status}: no required language tokens; "
                    "check 1 (no banned phrases) is the relevant gate."
                ),
            )
        )

    passed = all(c.passed for c in checks)
    return EvalReport(scenario="ri_trust_display", passed=passed, checks=checks)


# ----------------------------------------------------------------------
# RB 9.20 — CoS Daily Brief evaluation (DEFECT-008 / eval layer)
# Ported from wrong-dir canonical_response_eval.py (RB 9.20 sprint).
# These use a separate EvalResult type (float score) vs the older EvalReport.
# ----------------------------------------------------------------------

class EvalFinding(NamedTuple):
    rule: str
    severity: str   # "fail" | "warn"
    message: str
    evidence: str


class EvalResult(NamedTuple):
    scenario: str
    passed: bool
    score: float    # 0.0–1.0
    findings: list
    checks_passed: list
    checks_failed: list


# Phrases that must never appear in a CoS Daily Brief.
BANNED_PHRASES: dict = {
    "you have good momentum": "Generic encouragement without evidence — banned by CoS invariant",
    "keep leaning into": "Agreeable reinforcement — substitutes for strategic judgment",
    "lots of promising activity": "Activity ≠ progress — banned without evidence",
    "lots of promising signals": "Signal ≠ conversion — banned without named evidence",
    "promising momentum": "Generic momentum framing — banned",
    "here are some helpful suggestions": "Helpful-assistant framing — banned",
    "here are a few helpful recommendations": "Helpful-assistant framing — banned",
    "stay consistent": "Behavioral nudge without evidence or named next step — banned",
    "continue nurturing": "Without named next action and closure option — banned",
    "keep nurturing": "Without named next action and closure option — banned",
    "margin pressure exists": "Generic macro without causal mechanism or action — banned",
    "operators face pressure": "Generic without causal chain to user implication — fails unless followed by mechanism",
    "operators are still facing margin pressure": "Generic macro statement without operator/user implication chain",
    "restaurant technology buyers will continue to value roi": "Truism without causal mechanism or strategic action",
    "file processed successfully": "Static import language — LinkedIn ingestion must produce delta intelligence",
    "contacts imported": "Static import language — banned",
    "records parsed": "Static import language — must surface delta, not import count",
    "you have a lot of promising": "Generic encouragement — banned",
    "great momentum": "Generic encouragement without evidence — banned",
    "things are looking good": "Positive framing that ignores negative-space signals — banned",
}

REQUIRED_SECTIONS_TEXT = [
    ("resource verification", "Resource Verification & Freshness Status section missing"),
    ("cos judgment", "CoS Judgment section missing — brief must include hard truths"),
    ("what is not happening", "What Is Not Happening section missing — negative-space analysis required"),
    ("execution closure", "Execution Closure section missing — every recommendation must have a closure option"),
]

REQUIRED_JSON_FIELDS = [
    ("cos_judgment", "cos_judgment block missing from structured brief"),
    ("what_is_not_happening", "what_is_not_happening block missing"),
    ("execution_options", "execution_options block missing"),
    ("source_freshness", "source_freshness block missing"),
]

REQUIRED_COS_JUDGMENT_FIELDS = ["hard_truths", "negative_space", "confidence", "source_refs"]

REQUIRED_EXEC_OPTION_FIELDS = ["action", "target", "reason", "requires_confirmation"]

ALLOWED_EXEC_ACTIONS = frozenset({
    "create_task", "open_loop", "monitor_relationship", "schedule_reminder",
    "escalate_priority", "add_to_opportunity_pipeline", "open_outreach_loop", "ignore",
})


def eval_daily_brief_cos_operator_text(text: str) -> EvalResult:
    """Evaluate rendered Daily Brief text for CoS canonicality."""
    findings: list = []
    checks_passed: list = []
    checks_failed: list = []
    text_lower = text.lower()

    for phrase, rule in BANNED_PHRASES.items():
        if phrase in text_lower:
            findings.append(EvalFinding(
                rule="no_banned_phrases", severity="fail",
                message=f'Banned phrase found: "{phrase}"', evidence=rule,
            ))
            checks_failed.append(f"banned_phrase:{phrase[:40]}")
        else:
            checks_passed.append(f"no_banned_phrase:{phrase[:40]}")

    for keyword, message in REQUIRED_SECTIONS_TEXT:
        if keyword in text_lower:
            checks_passed.append(f"section_present:{keyword}")
        else:
            findings.append(EvalFinding(
                rule="required_sections", severity="fail",
                message=message,
                evidence=f"Expected section keyword '{keyword}' not found in text.",
            ))
            checks_failed.append(f"section_missing:{keyword}")

    freshness_indicators = [
        "source freshness", "fetched_at", "stale", "`fresh`", "`stale`",
        "source_unavailable", "resource verification",
    ]
    if any(ind in text_lower for ind in freshness_indicators):
        checks_passed.append("freshness_proof_present")
    else:
        findings.append(EvalFinding(
            rule="freshness_proof_required", severity="fail",
            message="No source freshness proof found in brief text.",
            evidence="Brief must include Resource Verification section with per-source freshness labels.",
        ))
        checks_failed.append("freshness_proof_missing")

    hard_truth_indicators = [
        "hard truth", "act_today", "evidence:", "this is not converting",
        "is not converting", "is past target", "is drifting", "confidence:",
        "[act_today]", "[monitor]",
    ]
    if any(ind in text_lower for ind in hard_truth_indicators):
        checks_passed.append("hard_truth_present")
    else:
        findings.append(EvalFinding(
            rule="hard_truth_required", severity="fail",
            message="No evidence-bound hard truth found in brief text.",
            evidence="CoS Judgment section must contain at least one hard truth with evidence.",
        ))
        checks_failed.append("hard_truth_missing")

    claims_quiet = any(phrase in text_lower for phrase in [
        "inbox is quiet", "nothing urgent", "quiet day", "no urgent",
        "all clear", "nothing pressing",
    ])
    has_freshness_stale_warning = any(ind in text_lower for ind in [
        "stale", "source_unavailable", "cannot confirm quiet", "cannot prove",
    ])
    if claims_quiet and not has_freshness_stale_warning:
        findings.append(EvalFinding(
            rule="no_quiet_without_freshness", severity="fail",
            message="Brief claims quiet without source freshness proof.",
            evidence="Quiet claims require verified email/direct-comms coverage.",
        ))
        checks_failed.append("quiet_without_freshness")

    op_pressure_phrases = ["operators face pressure", "margin pressure", "operators still facing"]
    for phrase in op_pressure_phrases:
        if phrase in text_lower:
            idx = text_lower.find(phrase)
            context = text_lower[idx:idx + 200]
            has_mechanism = any(kw in context for kw in [
                "→", "->", "mechanism:", "implication:", "therefore", "means", "results in",
                "freight", "distribution", "cost pressure", "experimentation",
            ])
            if not has_mechanism:
                findings.append(EvalFinding(
                    rule="macro_requires_mechanism", severity="fail",
                    message=f'Macro phrase "{phrase}" appears without a causal mechanism or user implication.',
                    evidence="Macro commentary must chain through mechanism → operator/tech/user implication.",
                ))
                checks_failed.append(f"macro_without_mechanism:{phrase[:30]}")
            else:
                checks_passed.append(f"macro_has_mechanism:{phrase[:30]}")

    total_checks = len(checks_passed) + len(checks_failed)
    score = len(checks_passed) / total_checks if total_checks > 0 else 0.0
    return EvalResult(
        scenario="daily_brief_cos_operator",
        passed=not any(f.severity == "fail" for f in findings),
        score=score,
        findings=findings,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
    )


def eval_daily_brief_cos_operator_json(payload: dict) -> EvalResult:
    """Evaluate structured Daily Brief JSON payload for CoS canonicality."""
    findings: list = []
    checks_passed: list = []
    checks_failed: list = []

    for field, message in REQUIRED_JSON_FIELDS:
        if field in payload or field in (payload.get("canonical_brief") or {}).get("sections", {}):
            checks_passed.append(f"field_present:{field}")
        else:
            findings.append(EvalFinding(
                rule="required_json_fields", severity="fail",
                message=message,
                evidence=f"Field '{field}' not found in brief payload.",
            ))
            checks_failed.append(f"field_missing:{field}")

    cj = payload.get("cos_judgment") or (
        (payload.get("canonical_brief") or {}).get("sections", {}).get("cos_judgment") or {}
    )
    for field in REQUIRED_COS_JUDGMENT_FIELDS:
        if field in cj:
            checks_passed.append(f"cos_judgment.{field}_present")
        else:
            findings.append(EvalFinding(
                rule="cos_judgment_structure", severity="fail",
                message=f"cos_judgment.{field} missing",
                evidence=f"CoS judgment block must include '{field}'.",
            ))
            checks_failed.append(f"cos_judgment.{field}_missing")

    hard_truths = cj.get("hard_truths") or []
    if hard_truths:
        checks_passed.append("hard_truths_non_empty")
        for i, ht in enumerate(hard_truths):
            if ht.get("evidence") and ht.get("claim"):
                checks_passed.append(f"hard_truth[{i}].evidence_present")
            else:
                findings.append(EvalFinding(
                    rule="hard_truth_evidence_bound", severity="fail",
                    message=f"Hard truth [{i}] missing claim or evidence.",
                    evidence="Every hard truth must have 'claim' and 'evidence' fields.",
                ))
                checks_failed.append(f"hard_truth[{i}].evidence_missing")
    else:
        findings.append(EvalFinding(
            rule="hard_truths_required", severity="fail",
            message="cos_judgment.hard_truths is empty.",
            evidence="CoS judgment must contain at least one hard truth.",
        ))
        checks_failed.append("hard_truths_empty")

    exec_opts = payload.get("execution_options") or (
        (payload.get("canonical_brief") or {}).get("sections", {}).get("execution_options") or []
    )
    if exec_opts:
        checks_passed.append("execution_options_non_empty")
        for i, opt in enumerate(exec_opts[:10]):
            for field in REQUIRED_EXEC_OPTION_FIELDS:
                if field in opt:
                    checks_passed.append(f"exec_opt[{i}].{field}_present")
                else:
                    findings.append(EvalFinding(
                        rule="execution_option_structure", severity="fail",
                        message=f"execution_options[{i}].{field} missing",
                        evidence="Every execution option must have action, target, reason, requires_confirmation.",
                    ))
                    checks_failed.append(f"exec_opt[{i}].{field}_missing")
            action = opt.get("action") or ""
            if action in ALLOWED_EXEC_ACTIONS:
                checks_passed.append(f"exec_opt[{i}].action_valid:{action}")
            elif action:
                findings.append(EvalFinding(
                    rule="execution_action_enum", severity="fail",
                    message=f"execution_options[{i}].action='{action}' is not in allowed enum.",
                    evidence=f"Allowed: {sorted(ALLOWED_EXEC_ACTIONS)}",
                ))
                checks_failed.append(f"exec_opt[{i}].action_invalid:{action}")
    else:
        findings.append(EvalFinding(
            rule="execution_options_required", severity="fail",
            message="execution_options is empty — recommendations must have closure options.",
            evidence="The brief must end with concrete execution choices.",
        ))
        checks_failed.append("execution_options_empty")

    sf = payload.get("source_freshness") or (
        (payload.get("canonical_brief") or {}).get("sections", {}).get("resource_verification_and_freshness_status") or {}
    )
    if sf.get("sources"):
        checks_passed.append("source_freshness_present")
    else:
        findings.append(EvalFinding(
            rule="source_freshness_required", severity="fail",
            message="source_freshness.sources is missing or empty.",
            evidence="Brief must include per-source freshness labels before any interpretation.",
        ))
        checks_failed.append("source_freshness_missing")

    total_checks = len(checks_passed) + len(checks_failed)
    score = len(checks_passed) / total_checks if total_checks > 0 else 0.0
    return EvalResult(
        scenario="daily_brief_cos_operator_json",
        passed=not any(f.severity == "fail" for f in findings),
        score=score,
        findings=findings,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
    )


# CoS brief smoke fixtures (RB 9.20)
BAD_BRIEF_FIXTURE = """
You have good momentum today. There are lots of promising signals across your
network, and the best move is to keep leaning into the relationships where
energy is already showing up.

Operators are still facing margin pressure, so restaurant technology buyers
will continue to value ROI. Here are some helpful suggestions:

- Follow up with a few high-value contacts.
- Stay consistent with your thought leadership.
- Keep nurturing the opportunities already in motion.
"""

GOOD_BRIEF_FIXTURE = """
## Resource Verification & Freshness Status

- **email**: `stale` — Last refresh 11.2h ago.
- **calendar**: `fresh` — 2026-05-28T05:01:00
- **linkedin_delta**: `fresh` — 2026-05-28T04:55:00
- **macro_synthesis**: `source_unavailable`

> **CoS constraint:** Direct communications are stale. The brief cannot confirm quiet.

## CoS Judgment

**Hard truths:**

- [ACT_TODAY] 14 loops are past their target date. Activity is not converting to closed commitments.
  - Evidence: 14 overdue loops in loop_ledger.md.
  - Confidence: high | Grounding: loop_ledger.md

## What Is Not Happening

- **[overdue_commitment]** Loop L-2026-05-08-020 is 13 days past target.
  - Expected: Follow-up with Patrick Nelson closed by 2026-05-15
  - Disposition: `act_today`

## Execution Closure

- `[escalate_priority]` [CONFIRM REQUIRED] **Patrick Nelson** — Loop past target (priority: high)
- `[open_loop]` [CONFIRM REQUIRED] **Chason Forehand** — 45 days past inner dormancy threshold
- `[open_outreach_loop]` [CONFIRM REQUIRED] **Jane Doe** — Promotion detected (priority: high)
"""


# Dispatcher for scenarios.
_EVALUATORS = {
    "no_response_update": evaluate_no_response_update,
    "ri_intake_detected": evaluate_ri_intake_detected,
    "known_artifact_ingest": evaluate_known_artifact_ingest,
    "passive_ri_summary": evaluate_passive_ri_summary,
    "daily_brief_top_contract": evaluate_daily_brief_top_contract,
    "strategic_memory_record": evaluate_strategic_memory_record,
    "ri_trust_display": evaluate_ri_trust_display,
}


def evaluate(text: str, scenario: str) -> EvalReport:
    if scenario not in _EVALUATORS:
        raise ValueError(
            f"unsupported scenario: {scenario!r}; "
            f"known: {sorted(_EVALUATORS)}"
        )
    return _EVALUATORS[scenario](text)


# ----------------------------------------------------------------------
# Smoke fixtures
# ----------------------------------------------------------------------

# Failing fixture — a synthesized assistant response in the *shape* of the
# coaching/advisor drift captured by trace T-2026-05-20-002. The trace
# itself stores the operator's evaluation summary, not the raw assistant
# response, so this fixture is the canonical representative of that defect
# class: no required sections, no grounding labels, no dispositions, and
# multiple banned coaching phrases used in running prose (not quoted).
SMOKE_FAILING_FIXTURE = """\
Todd, here's how I'd think about this. The silence from Simin on Harri
probably means you're being treated as a candidate they're holding warm
while they sort out internal priorities — timing says wait it out.

On Jeff Coffland, lean into your existing relationship; he can be the
strategic advisor inside McDonald's that opens the second door.

For Global Payments, the lack of response is really about market
positioning — they may not yet see how you fit. Trust the process and
keep showing up.
"""

# Passing fixture — a canonical no-response-update response carrying every
# required section, an explicit grounding label, and explicit dispositions.
SMOKE_PASSING_FIXTURE = """\
Observed signals:
- Harri / Simin — no response reported by Todd since 2026-05-13 (manual_user_provided; confidence medium)
- Jeff Coffland — Todd waiting on possible McDonald's-side advocacy (manual_user_provided; confidence medium)
- Global Payments — follow-up sent, no response reported (manual_user_provided; confidence medium)

State changes:
- Harri: active -> pending/no_response
- Jeff Coffland: secondary_advocacy_dependency unchanged
- Global Payments: followup_sent -> awaiting_response

Inferences:
- No explicit rejection detected. Silence lowers Harri confidence but does not close the opportunity.
- Global Payments confidence unchanged if follow-up is still inside normal response window.

Recommended actions:
- [monitor] Harri / Simin — no additional follow-up for 3-5 business days.
- [monitor] Jeff Coffland — leave as secondary dependency unless the response window expires.
- [monitor] Global Payments — wait or follow up based on sent date; do not infer rejection.

Persistence:
- status: proposed_write_pending_confirmation
- proposed mutations: RI events for the three signals above; no automatic writes.
"""


# ----------------------------------------------------------------------
# Additional smoke fixtures — new scenarios (RB 9.5)
# ----------------------------------------------------------------------

# ri_intake_detected — passing fixture
SMOKE_RI_INTAKE_PASSING = """\
Manual RI Signal — Ish Singh / Maho
RB match: matched (ish-singh)
Grounding: manual_user_provided
Confidence: high
Event date: 2026-05-18 (event_at_confidence: high)

Signal read:
- Signal type: inbound_capability_offer
- Substance: high
- Strategic relevance: high
- Why it matters: Maho invited a deeper dive on Todd's background, the Maho
  platform, and commercial opportunities — high-value inbound interest.

RB proposed:
- touchContact(ish-singh, 2026-05-18)
- Open loop: schedule deeper-dive call with Ish at Maho

Persistence status: proposed_write_pending_confirmation
Bundle ID: mb_2026-05-18_ish-singh_001

Recommended action: [act_today] Reply and schedule. Confirm proposed writes.

Reconciliation needed: Should this become an active thread under Maho advisory?
"""

# ri_intake_detected — failing fixture (no sections, system_detected on manual input,
# no persistence_status, banned phrases)
SMOKE_RI_INTAKE_FAILING = """\
Ish Singh from Maho sent you a message. This is a strong signal and you should be marked
as a strategic advisor for their platform rollout. It would likely lead to a consulting
opportunity. I recommend reaching out to schedule a call.
"""

# passive_ri_summary — passing fixture
SMOKE_PASSIVE_RI_PASSING = """\
Passive RI Ingest — 2026-05-24
Source: relationship_signals (system_detected), linkedin_messaging (stale_source_limited)

RB reviewed 3 passive signal candidates.
RB proposed 1 review-first RI event:
  - last_touch_update — Bob Gibson (2026-05-23) · system_detected; confidence high
    Pending confirmation. Bundle: mb_ri_20260523_bob-gibson_001.
RB skipped duplicate: Bob Gibson last_touch_update 2026-05-23 already exists.
RB blocked 1 candidate: signal_strength=0.32 below threshold 0.40 (unnamed contact).

Source availability:
  - relationship_signals: OK
  - linkedin_messaging: stale_source_limited (47h, threshold 24h)
    RB could not prove quiet on LinkedIn because source is stale.
"""

# passive_ri_summary — failing fixture (no action state, claims quiet despite stale)
SMOKE_PASSIVE_RI_FAILING = """\
The passive scan found no new relationship signals today. Everything looks quiet.
No updates were needed. No candidates were reviewed.
"""

# daily_brief_top_contract — passing fixture
SMOKE_DAILY_BRIEF_PASSING = """\
## CoS Bottom Line

Global Payments onboarding is the week's job — 4/22 overdue loops resolved and closing.
Operational AI implementation risk in restaurants is the signal worth tracking today.
LinkedIn messaging is stale (168h, threshold 48h) so RB could not prove quiet there —
treat that channel's silence as unverified, not confirmed. Move the needle: reply to
Ryan Hildebrand before end of day.
"""

# daily_brief_top_contract — failing fixture (visible metadata labels, no CoS Bottom
# Line, stale source present but claims quiet)
SMOKE_DAILY_BRIEF_FAILING = """\
Good morning! Here are your top priorities for today.

- Ish Singh outreach — Grounding: system_detected | Freshness: fresh | Confidence: high | Disposition: act_today

There were no new signals from your network overnight — it looks like a quiet day.
The linkedin feed is stale but nothing important is being missed.
"""

# strategic_memory_record — passing fixture
SMOKE_STRATEGIC_MEMORY_PASSING = """\
Industry intelligence detected. RB recorded the following durable signal.

Signal: Starbucks discontinued an AI inventory tool across North America
after operational accuracy and adoption issues.

What it proves: Restaurant AI must survive real-world operating conditions,
not just demos or pilots.

Strategic implication: Enterprise buyers will increasingly scrutinize AI
reliability, workflow burden, ROI, and operator trust.

User POV alignment: Supports Todd's thesis that restaurant tech succeeds
only when it is simple, trusted, operationally durable, and economically provable.

RB recorded strategic memory:
- ID: si_4a3f2b1c88d
- Categories: static_industry_assessment, strategic_company_watchlist, market_pattern
- Stored in: system/strategic_memory.json

Persistence status: persisted
Grounding: manual_user_provided

Future-use requirements: RB should reuse this signal when evaluating:
- AI restaurant tech vendors
- Computer vision companies
- Enterprise rollout claims
- LinkedIn post opportunities
"""

# strategic_memory_record — failing fixture (no action verb, no persistence, non-action language)
SMOKE_STRATEGIC_MEMORY_FAILING = """\
This should be remembered as an important signal about Starbucks and AI inventory.
It validates the thesis that restaurant tech is hard. You might want to keep this in
mind when talking to AI vendors.
"""


def _format_report_human(report: EvalReport) -> str:
    lines = [
        f"scenario: {report.scenario}",
        f"overall:  {'PASS' if report.passed else 'FAIL'}",
        "",
    ]
    for c in report.checks:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.name} — {c.detail}")
    return "\n".join(lines)


def _run_smoke_pair(
    scenario: str,
    passing_fixture: str,
    failing_fixture: str,
    label_pass: str,
    label_fail: str,
) -> tuple[bool, bool]:
    """Evaluate one passing and one failing fixture. Return (pass_ok, fail_ok)."""
    pass_report = evaluate(passing_fixture, scenario)
    fail_report = evaluate(failing_fixture, scenario)

    pass_ok = pass_report.passed
    fail_ok = not fail_report.passed  # failing fixture should NOT pass

    print(f"== Smoke [{scenario}]: failing fixture ({label_fail}) ==")
    print(_format_report_human(fail_report))
    print()
    print(f"== Smoke [{scenario}]: passing fixture ({label_pass}) ==")
    print(_format_report_human(pass_report))
    print()
    print(
        f"[{scenario}] failing fixture failed as expected: "
        f"{'YES' if fail_ok else 'NO (REGRESSION)'}"
    )
    print(
        f"[{scenario}] passing fixture passed as expected: "
        f"{'YES' if pass_ok else 'NO (REGRESSION)'}"
    )
    print()
    return pass_ok, fail_ok


def _smoke() -> int:
    all_ok = True

    # 1. no_response_update
    p, f = _run_smoke_pair(
        "no_response_update",
        SMOKE_PASSING_FIXTURE,
        SMOKE_FAILING_FIXTURE,
        "canonical no-response update",
        "T-2026-05-20-002 defect class",
    )
    all_ok = all_ok and p and f

    # 2. ri_intake_detected
    p, f = _run_smoke_pair(
        "ri_intake_detected",
        SMOKE_RI_INTAKE_PASSING,
        SMOKE_RI_INTAKE_FAILING,
        "canonical manual RI intake",
        "no-section / system_detected mis-label / banned phrases",
    )
    all_ok = all_ok and p and f

    # 3. passive_ri_summary
    p, f = _run_smoke_pair(
        "passive_ri_summary",
        SMOKE_PASSIVE_RI_PASSING,
        SMOKE_PASSIVE_RI_FAILING,
        "canonical passive RI summary",
        "no action state / quiet claim despite stale",
    )
    all_ok = all_ok and p and f

    # 4. daily_brief_top_contract
    p, f = _run_smoke_pair(
        "daily_brief_top_contract",
        SMOKE_DAILY_BRIEF_PASSING,
        SMOKE_DAILY_BRIEF_FAILING,
        "canonical daily brief top contract",
        "no resource verification / quiet claim despite stale source",
    )
    all_ok = all_ok and p and f

    # 5. strategic_memory_record
    p, f = _run_smoke_pair(
        "strategic_memory_record",
        SMOKE_STRATEGIC_MEMORY_PASSING,
        SMOKE_STRATEGIC_MEMORY_FAILING,
        "canonical strategic memory record",
        "no action verb / non-action language",
    )
    all_ok = all_ok and p and f

    # 6. ri_trust_display (P-036) — proposed status
    _P036_PASSING = """\
I found a possible relationship signal — Jeff Wayman replied to your
May 12 message (system_detected; confidence 0.71; source: email; fresh).
I would propose recording a last_touch update for Jeff Wayman on 2026-05-12.
Pending confirmation — confirm to apply.
Recommended action: Confirm if you want me to apply this update.
"""
    _P036_FAILING = """\
Trust updated for Jeff Wayman — relationship state updated.
I captured that message and recorded the interaction.
I've updated your notes on Jeff Wayman.
"""
    # ri_trust_display uses a keyword arg; wrap in lambda for _run_smoke_pair.
    _p036_pass_report = evaluate_ri_trust_display(_P036_PASSING, ri_status="proposed")
    _p036_fail_report = evaluate_ri_trust_display(_P036_FAILING, ri_status="proposed")
    p6 = _p036_pass_report.passed
    f6 = not _p036_fail_report.passed
    print("== Smoke [ri_trust_display]: failing fixture (P-036 banned trust-claim phrases) ==")
    print(_format_report_human(_p036_fail_report))
    print()
    print("== Smoke [ri_trust_display]: passing fixture (canonical P-036 proposed language) ==")
    print(_format_report_human(_p036_pass_report))
    print()
    print(f"[ri_trust_display] failing fixture failed as expected: {'YES' if f6 else 'NO (REGRESSION)'}")
    print(f"[ri_trust_display] passing fixture passed as expected: {'YES' if p6 else 'NO (REGRESSION)'}")
    print()
    all_ok = all_ok and p6 and f6

    print("=" * 60)
    print(f"Overall smoke result: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _read_text(args) -> str:
    if args.text_stdin:
        return sys.stdin.read()
    if args.text:
        path = Path(args.text)
        if not path.is_file():
            sys.stderr.write(f"text file not found: {path}\n")
            sys.exit(2)
        return path.read_text(encoding="utf-8")
    sys.stderr.write(
        "no input: pass --smoke, --text PATH, or --text-stdin\n"
    )
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Canonical RB response evaluator (regression harness)."
    )
    p.add_argument(
        "--smoke",
        action="store_true",
        help="Run the bundled failing + passing smoke fixtures.",
    )
    p.add_argument(
        "--scenario",
        default="no_response_update",
        choices=sorted(_EVALUATORS.keys()),
        help=(
            "Scenario to evaluate (default: no_response_update). "
            f"Available: {', '.join(sorted(_EVALUATORS.keys()))}"
        ),
    )
    p.add_argument(
        "--text",
        help="Path to a text file containing the assistant response to evaluate.",
    )
    p.add_argument(
        "--text-stdin",
        action="store_true",
        help="Read the assistant response from stdin.",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the evaluation report as JSON instead of human-readable text.",
    )

    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    text = _read_text(args)
    report = evaluate(text, args.scenario)
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_format_report_human(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
