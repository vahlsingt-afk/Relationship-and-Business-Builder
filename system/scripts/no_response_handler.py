#!/usr/bin/env python3
"""
no_response_handler.py — canonical-shape renderer for Todd-stated
silence / waiting / no-reply updates.

Step 4 of the canonical CoS sprint. Closes the defect captured in
trace T-2026-05-20-002 (system/test_traces/
2026-05-20-negative-no-response-canonical-cos-drift.md): the assistant
drifted into coaching narrative instead of producing operational state.

This module is a deterministic parser + renderer. It does NOT call an
LLM and does NOT write to canonical state. It returns a structured
report and a rendered text block that is intended to pass
`canonical_response_eval.py --scenario no_response_update`.

Entry points:

    detect_clauses(text)
        → list[Clause]

    build_report(text, *, baseline=None, threads=None, now=None)
        → ReportDict (Observed signals / State changes / Inferences /
          Recommended actions / Persistence + the rendered canonical
          text in `canonical_text`)

CLI:

    python3 no_response_handler.py --smoke
    python3 no_response_handler.py --text path/to/update.txt
    python3 no_response_handler.py --text-stdin --json < update.txt

Persistence stance: every report comes back with status
`proposed_write_pending_confirmation`. No automatic writes. The
proposed mutations list names the RI event types the operator would
need to confirm (waiting_on_recruiter, followup_sent,
awaiting_response, secondary_advocacy_dependency). See the handoff
Step 4 section for the rationale.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# ----------------------------------------------------------------------
# Patterns
# ----------------------------------------------------------------------

# Capture a capitalized name token sequence. Handles single-word ("Simin")
# and multi-word ("Jeff Coffland", "Global Payments") names. We deliberately
# allow "Mc"-style internal capitalization (McDonald's), apostrophes, and
# the connector "of" inside multi-word names.
_NAME_TOK = r"[A-Z][A-Za-z'’]+(?:[A-Z][A-Za-z'’]*)?"
_NAME_RX = rf"{_NAME_TOK}(?:\s+(?:of\s+)?{_NAME_TOK}){{0,3}}"

# IMPORTANT: the entity capture must stay case-sensitive (capitalized
# tokens only); otherwise lowercase connector words like "regarding"
# get pulled into the entity span. We therefore use inline (?i:...)
# scopes for the keyword tokens only and do NOT pass re.IGNORECASE on
# the compiled patterns.

# No-response clause patterns. Each pattern captures the entity name as
# group "entity". Order matters: more-specific patterns first.
NO_RESPONSE_PATTERNS = [
    re.compile(
        r"\b(?i:no)\s+(?i:response|reply|word|update)\s+(?i:yet\s+)?"
        rf"(?i:from|on)\s+(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        r"\b(?i:haven)'?(?i:t)\s+(?i:heard)\s+(?i:back\s+)?(?i:from)\s+"
        rf"(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        r"\b(?i:have)\s+(?i:not)\s+(?i:heard)\s+(?i:back\s+)?(?i:from)\s+"
        rf"(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        r"\b(?i:silent)\s+(?i:from)\s+"
        rf"(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        rf"\b(?i:no)\s+(?i:reply)\s+(?i:yet)\s+(?i:from)\s+(?P<entity>{_NAME_RX})\b",
    ),
]

# Waiting / dependency clause patterns. "waiting on X" can mean either a
# generic wait OR a secondary-advocacy dependency depending on what
# follows. We capture the entity and the suffix together; the classifier
# decides which subtype to assign.
WAITING_PATTERNS = [
    re.compile(
        rf"\b(?i:(?:still\s+)?waiting)\s+(?i:on|for)\s+(?P<entity>{_NAME_RX})"
        r"(?P<suffix>[^.;\n]*)?",
    ),
    re.compile(
        rf"\b(?i:pending)\s+(?P<entity>{_NAME_RX})\b",
    ),
]

# Followup-sent clause patterns. Todd reports an outbound send.
FOLLOWUP_SENT_PATTERNS = [
    re.compile(
        rf"\b(?i:sent)\s+(?i:my|the|a)\s+(?i:follow-?up)\s+(?i:to|email\s+to)\s+"
        rf"(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        rf"\b(?i:followed)\s+(?i:up)\s+(?i:with)\s+(?P<entity>{_NAME_RX})\b",
    ),
    re.compile(
        rf"\b(?i:my)\s+(?i:follow-?up)\s+(?i:email\s+)?(?i:to)\s+(?P<entity>{_NAME_RX})\b",
    ),
]

# Suffix keywords that signal secondary-advocacy framing.
ADVOCACY_HINT_RX = re.compile(
    r"\b(advocacy|advocate|champion|vouch|vouching|sponsor|sponsorship"
    r"|second(?:ary)?\s+(?:advoc|lane|opinion)|internal\s+(?:advoc|champion))\b",
    re.IGNORECASE,
)

# "<entity> follow-up email" reads as a Global Payments-shape clause: a
# follow-up was sent and no reply has come. Combined with a leading
# "no response from", this pattern is captured by NO_RESPONSE_PATTERNS,
# but we use this auxiliary regex to *enrich* the clause with the fact
# that it was a follow-up rather than a fresh outreach.
NO_RESPONSE_FOLLOWUP_HINT_RX = re.compile(
    r"\bfollow-?up\s+(?:email|note|message|outreach|ping)\b",
    re.IGNORECASE,
)

# Companies often show up as "regarding <Company>" / "on <Company>" /
# "<Company>-side". We extract these as context to attach to the
# primary entity. Same case-sensitivity rule: keywords IGNORECASE
# inline, entity capture case-sensitive.
COMPANY_CONTEXT_PATTERNS = [
    re.compile(rf"\b(?i:regarding)\s+(?P<co>{_NAME_RX})\b"),
    re.compile(rf"\b(?i:re):?\s+(?P<co>{_NAME_RX})\b"),
    re.compile(rf"\b(?i:on)\s+(?P<co>{_NAME_RX})(?:\s+(?i:opportunity))?\b"),
    re.compile(rf"\b(?i:for)\s+(?P<co>{_NAME_RX})-?(?i:side)\b"),
]

# Generic English words that show up capitalized at the start of a
# sentence but aren't names. Suppress these when extracting entities.
NOT_NAMES = {
    "No", "Waiting", "Still", "Pending", "Sent", "Followed", "I", "We",
    "Today", "Yesterday", "Tomorrow", "Last", "Next",
    "The", "A", "An", "On", "From", "To", "For", "And", "Or", "But",
    "Mc", "Mac",  # avoid false splits on McDonald's
}


# ----------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------

@dataclass
class Clause:
    """A single classified silence / waiting / followup phrase."""
    kind: str            # no_response | waiting_on | followup_sent
    subtype: str | None  # secondary_advocacy_dependency, awaiting_response, etc.
    entity: str          # primary entity name (person or company)
    company_context: str | None  # secondary "regarding X" reference
    raw_phrase: str      # the matched span of source text
    grounding: str = "manual_user_provided"
    confidence: str = "medium"  # high|medium|low

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------

def _clean_entity(name: str) -> str:
    """Trim trailing punctuation and obvious noise from a captured name."""
    n = (name or "").strip()
    n = re.sub(r"[\s,.;:'’]+$", "", n)
    return n


def _find_company_context(span: str) -> str | None:
    """Look for a 'regarding X' / 'on X' / 'X-side' company hint in the
    sentence following the matched clause."""
    for rx in COMPANY_CONTEXT_PATTERNS:
        m = rx.search(span)
        if not m:
            continue
        co = _clean_entity(m.group("co"))
        if co and co.split()[0] not in NOT_NAMES:
            return co
    return None


def _sentence_around(text: str, start: int, end: int) -> str:
    """Return the sentence (or short clause) the match falls inside,
    bounded by . ; \\n or the text endpoints."""
    # Walk back to the most recent sentence break.
    pre = text[:start]
    last_break = max(pre.rfind("."), pre.rfind(";"), pre.rfind("\n"))
    s = last_break + 1 if last_break >= 0 else 0
    # Walk forward to the next sentence break.
    post = text[end:]
    nexts = [i for i in (post.find("."), post.find(";"), post.find("\n")) if i >= 0]
    e = end + (min(nexts) if nexts else len(post))
    return text[s:e].strip()


def detect_clauses(text: str) -> list[Clause]:
    """Parse `text` and return classified clauses.

    Deterministic, regex-driven. False negatives are preferred over
    false positives — when in doubt, skip rather than mis-classify.
    """
    if not text or not text.strip():
        return []

    out: list[Clause] = []
    seen_spans: list[tuple[int, int]] = []  # to suppress overlapping matches

    def _claim(start: int, end: int) -> bool:
        for s, e in seen_spans:
            if not (end <= s or start >= e):
                return False
        seen_spans.append((start, end))
        return True

    # 1) No-response clauses (highest specificity).
    for rx in NO_RESPONSE_PATTERNS:
        for m in rx.finditer(text):
            if not _claim(m.start(), m.end()):
                continue
            entity = _clean_entity(m.group("entity"))
            if not entity or entity.split()[0] in NOT_NAMES:
                continue
            sentence = _sentence_around(text, m.start(), m.end())
            company = _find_company_context(sentence)
            is_followup = bool(NO_RESPONSE_FOLLOWUP_HINT_RX.search(sentence))
            out.append(Clause(
                kind="no_response",
                subtype="awaiting_response" if is_followup else None,
                entity=entity,
                company_context=company,
                raw_phrase=sentence,
            ))

    # 2) Followup-sent clauses (the operator explicitly says they sent
    #    something). These are stronger evidence of awaiting-response
    #    than a no_response_followup phrasing because they assert the send.
    for rx in FOLLOWUP_SENT_PATTERNS:
        for m in rx.finditer(text):
            if not _claim(m.start(), m.end()):
                continue
            entity = _clean_entity(m.group("entity"))
            if not entity or entity.split()[0] in NOT_NAMES:
                continue
            sentence = _sentence_around(text, m.start(), m.end())
            company = _find_company_context(sentence)
            out.append(Clause(
                kind="followup_sent",
                subtype="awaiting_response",
                entity=entity,
                company_context=company,
                raw_phrase=sentence,
            ))

    # 3) Waiting / dependency clauses.
    for rx in WAITING_PATTERNS:
        for m in rx.finditer(text):
            if not _claim(m.start(), m.end()):
                continue
            entity = _clean_entity(m.group("entity"))
            if not entity or entity.split()[0] in NOT_NAMES:
                continue
            suffix = (m.groupdict().get("suffix") or "")
            sentence = _sentence_around(text, m.start(), m.end())
            company = _find_company_context(sentence)
            # Advocacy framing? Look in both the captured suffix and the
            # full surrounding sentence to be generous.
            is_advocacy = bool(
                ADVOCACY_HINT_RX.search(suffix)
                or ADVOCACY_HINT_RX.search(sentence)
            )
            out.append(Clause(
                kind="waiting_on",
                subtype="secondary_advocacy_dependency" if is_advocacy else None,
                entity=entity,
                company_context=company,
                raw_phrase=sentence,
            ))

    # Order by appearance in the source text for stable rendering.
    out.sort(key=lambda c: text.find(c.raw_phrase) if text.find(c.raw_phrase) >= 0 else 1 << 30)
    return out


# ----------------------------------------------------------------------
# State / inference / action mapping
# ----------------------------------------------------------------------

def _entity_label(c: Clause) -> str:
    """Render a 'Primary [/ Secondary]' label for an entity + its
    company context, used in the rendered output."""
    if c.company_context and c.company_context.lower() != c.entity.lower():
        return f"{c.entity} / {c.company_context}"
    return c.entity


def _state_change(c: Clause) -> str:
    label = _entity_label(c)
    if c.kind == "no_response":
        if c.subtype == "awaiting_response":
            return f"{label}: followup_sent -> awaiting_response"
        return f"{label}: active -> pending/no_response"
    if c.kind == "followup_sent":
        return f"{label}: followup_sent -> awaiting_response"
    # waiting_on
    if c.subtype == "secondary_advocacy_dependency":
        return f"{label}: secondary_advocacy_dependency unchanged"
    return f"{label}: awaiting_response (waiting)"


def _inference(c: Clause) -> str:
    label = _entity_label(c)
    if c.kind == "no_response" and c.subtype == "awaiting_response":
        return (
            f"{label}: follow-up sent, no reply yet. Confidence unchanged "
            f"if still inside the normal response window."
        )
    if c.kind == "no_response":
        return (
            f"{label}: no explicit rejection detected. Silence lowers "
            f"confidence but does not close the opportunity."
        )
    if c.kind == "followup_sent":
        return (
            f"{label}: follow-up sent. Confidence unchanged pending a "
            f"reply; do not infer rejection from continued silence."
        )
    if c.subtype == "secondary_advocacy_dependency":
        return (
            f"{label}: secondary advocacy lane; treat as a dependency, "
            f"not a guarantee."
        )
    return f"{label}: awaiting movement; no inference of rejection."


def _action(c: Clause) -> tuple[str, str]:
    """Return (disposition, action_text). Disposition is one of the four
    canonical values: act_today, monitor, ask_todd, ignore."""
    label = _entity_label(c)
    if c.kind == "no_response" and c.subtype == "awaiting_response":
        # Like Global Payments: follow-up sent, no reply. Default is
        # monitor; the operator can escalate to act_today once they know
        # the sent date.
        return (
            "monitor",
            f"{label} — wait or follow up depending on sent date; "
            f"do not infer rejection from silence.",
        )
    if c.kind == "no_response":
        return (
            "monitor",
            f"{label} — no additional follow-up for 3-5 business days. "
            f"Silence is not rejection.",
        )
    if c.kind == "followup_sent":
        return (
            "monitor",
            f"{label} — wait for response inside the expected window; "
            f"follow up only if the window expires.",
        )
    if c.subtype == "secondary_advocacy_dependency":
        return (
            "monitor",
            f"{label} — leave as a secondary advocacy dependency unless "
            f"the response window expires.",
        )
    return (
        "monitor",
        f"{label} — continue waiting; no proactive follow-up today.",
    )


def _proposed_mutation(c: Clause) -> dict:
    """Map the clause to a proposed (not-yet-written) RI event type."""
    if c.kind == "no_response" and c.subtype == "awaiting_response":
        return {"type": "awaiting_response", "entity": _entity_label(c)}
    if c.kind == "no_response":
        return {"type": "no_response_logged", "entity": _entity_label(c)}
    if c.kind == "followup_sent":
        return {"type": "followup_sent", "entity": _entity_label(c)}
    if c.subtype == "secondary_advocacy_dependency":
        return {"type": "secondary_advocacy_dependency", "entity": _entity_label(c)}
    return {"type": "waiting_on", "entity": _entity_label(c)}


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------

def render_canonical(report: dict) -> str:
    """Render a no-response report into the canonical 5-section text
    block (Observed signals / State changes / Inferences / Recommended
    actions / Persistence). Intended to pass
    canonical_response_eval.py --scenario no_response_update.
    """
    lines: list[str] = []

    lines.append("Observed signals:")
    for c in report["observed_signals"]:
        lines.append(
            f"- {_entity_label_dict(c)} — {c['raw_phrase']} "
            f"(`{c['grounding']}`; confidence {c['confidence']})"
        )
    lines.append("")

    lines.append("State changes:")
    for s in report["state_changes"]:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("Inferences:")
    for inf in report["inferences"]:
        lines.append(f"- {inf}")
    lines.append("")

    lines.append("Recommended actions:")
    for a in report["recommended_actions"]:
        lines.append(f"- [{a['disposition']}] {a['action_text']}")
    lines.append("")

    lines.append("Persistence:")
    p = report["persistence"]
    lines.append(f"- status: {p['status']}")
    mut_strs = [
        f"{m['type']} for {m['entity']}" for m in p["proposed_mutations"]
    ] or ["(no proposed mutations)"]
    lines.append(f"- proposed mutations: {', '.join(mut_strs)}")

    return "\n".join(lines)


def _entity_label_dict(clause_dict: dict) -> str:
    """Same as _entity_label but for a dict (post-asdict). Avoids
    accidentally double-listing the company when it equals the entity."""
    e = clause_dict.get("entity") or ""
    co = clause_dict.get("company_context") or ""
    if co and co.lower() != e.lower():
        return f"{e} / {co}"
    return e


# ----------------------------------------------------------------------
# Top-level pipeline
# ----------------------------------------------------------------------

def build_report(text: str, *, baseline=None, threads=None,
                 now: datetime | None = None) -> dict:
    """Run the full no-response pipeline over `text`.

    Returns a dict with:
        observed_signals: list of clause dicts (the input we parsed)
        state_changes:    list of strings
        inferences:       list of strings
        recommended_actions: list of {disposition, action_text}
        persistence:      {status, proposed_mutations: [...]}
        canonical_text:   rendered text block (passes canonical_response_eval)
        ungrounded:       True if no clauses were detected
    """
    _ = (baseline, threads, now)  # reserved for future grounding lookups
    clauses = detect_clauses(text)

    observed = [c.to_dict() for c in clauses]

    if not clauses:
        # Nothing to render; return a minimal "ungrounded" report so the
        # caller can decide what to do (e.g. fall through to assess()).
        report = {
            "observed_signals": [],
            "state_changes": [],
            "inferences": [],
            "recommended_actions": [],
            "persistence": {
                "status": "not_persisted",
                "proposed_mutations": [],
            },
            "ungrounded": True,
            "canonical_text": "",
        }
        return report

    state_changes = [_state_change(c) for c in clauses]
    inferences = [_inference(c) for c in clauses]
    actions = []
    for c in clauses:
        disp, txt = _action(c)
        actions.append({"disposition": disp, "action_text": txt})
    mutations = [_proposed_mutation(c) for c in clauses]

    report = {
        "observed_signals": observed,
        "state_changes": state_changes,
        "inferences": inferences,
        "recommended_actions": actions,
        "persistence": {
            "status": "proposed_write_pending_confirmation",
            "proposed_mutations": mutations,
        },
        "ungrounded": False,
    }
    report["canonical_text"] = render_canonical(report)
    return report


# ----------------------------------------------------------------------
# Smoke test
# ----------------------------------------------------------------------

# Canonical example text drawn from T-2026-05-20-002 and the handoff
# Step 4 fixture.
SMOKE_INPUT = (
    "No response from Simin regarding Harri. "
    "Waiting on Jeff Coffland for possible McDonald's-side advocacy. "
    "No response from Global Payments follow-up email."
)


def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    clauses = detect_clauses(SMOKE_INPUT)
    by_entity = {c.entity.lower(): c for c in clauses}

    # 1. Detection.
    ck(len(clauses) == 3, f"detect_clauses returns 3 clauses (got {len(clauses)})")
    ck("simin" in by_entity, "Simin detected")
    ck("jeff coffland" in by_entity, "Jeff Coffland detected")
    ck("global payments" in by_entity, "Global Payments detected")

    # 2. Classifications.
    simin = by_entity.get("simin")
    coffland = by_entity.get("jeff coffland")
    global_payments = by_entity.get("global payments")
    if simin:
        ck(simin.kind == "no_response",
           f"Simin kind=no_response (got {simin.kind})")
        ck(simin.company_context == "Harri",
           f"Simin company_context=Harri (got {simin.company_context})")
    if coffland:
        ck(coffland.kind == "waiting_on",
           f"Jeff Coffland kind=waiting_on (got {coffland.kind})")
        ck(coffland.subtype == "secondary_advocacy_dependency",
           f"Jeff Coffland subtype=secondary_advocacy_dependency "
           f"(got {coffland.subtype})")
    if global_payments:
        ck(global_payments.kind == "no_response",
           f"Global Payments kind=no_response (got {global_payments.kind})")
        ck(global_payments.subtype == "awaiting_response",
           f"Global Payments subtype=awaiting_response "
           f"(got {global_payments.subtype})")

    # 3. Full pipeline.
    report = build_report(SMOKE_INPUT)
    ck(not report["ungrounded"], "report is not ungrounded")
    ck(len(report["state_changes"]) == 3, "3 state changes rendered")
    ck(len(report["recommended_actions"]) == 3, "3 recommended actions rendered")
    ck(report["persistence"]["status"] == "proposed_write_pending_confirmation",
       "persistence status is proposed_write_pending_confirmation")
    ck(all(a["disposition"] in {"act_today", "monitor", "ask_todd", "ignore"}
           for a in report["recommended_actions"]),
       "all dispositions are canonical")

    # 4. Canonical text passes the Step 1 evaluator.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import canonical_response_eval as cre
    eval_report = cre.evaluate(report["canonical_text"], "no_response_update")
    failed_checks = [c for c in eval_report.checks if not c.passed]
    ck(eval_report.passed,
       "rendered canonical text passes canonical_response_eval "
       f"(failed: {[c.name for c in failed_checks]})")

    # 5. State changes match the trace's expected canonical shape.
    sc_text = "\n".join(report["state_changes"]).lower()
    ck("harri" in sc_text and "pending/no_response" in sc_text,
       "Harri state transition includes pending/no_response")
    ck("coffland" in sc_text and "secondary_advocacy_dependency" in sc_text,
       "Jeff Coffland state mentions secondary_advocacy_dependency")
    ck("global payments" in sc_text and "awaiting_response" in sc_text,
       "Global Payments state transitions to awaiting_response")

    # 6. Empty input → ungrounded report, not a crash.
    empty = build_report("")
    ck(empty["ungrounded"] and empty["canonical_text"] == "",
       "empty input returns ungrounded report")

    print(f"--- no_response_handler smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _read_text(args) -> str:
    if args.text_stdin:
        return sys.stdin.read()
    if args.text:
        p = Path(args.text)
        if not p.is_file():
            sys.stderr.write(f"text file not found: {p}\n")
            sys.exit(2)
        return p.read_text(encoding="utf-8")
    sys.stderr.write("no input: pass --smoke, --text PATH, or --text-stdin\n")
    sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Canonical-shape renderer for no-response/waiting updates."
    )
    p.add_argument("--smoke", action="store_true",
                   help="Run the bundled fixture and assertions.")
    p.add_argument("--text", help="Path to a text file with the operator update.")
    p.add_argument("--text-stdin", action="store_true",
                   help="Read the operator update from stdin.")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit the structured report as JSON.")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()

    text = _read_text(args)
    report = build_report(text)
    if args.as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        if report["ungrounded"]:
            print("(no no-response signals detected in input)")
        else:
            print(report["canonical_text"])
    return 0 if not report["ungrounded"] else 1


if __name__ == "__main__":
    sys.exit(main())
