#!/usr/bin/env python3
"""
manual_relationship_intake.py — deterministic RI intake for pasted/screenshot text.

Connected feeds already flow through relationship_signals.py. Manual artifacts
(SMS screenshots, copied LinkedIn DMs, pasted recruiter notes) need a different
surface: compact, measurable, review-first, and mutation-ready.

This script does not write canonical state. It turns user-provided text into:

  - structured relationship signals
  - trust / advocacy / sponsorship / strategic-value metrics
  - before/after DRR projection
  - proposed write operations the caller can confirm and execute

The point is to prevent the old failure mode: good relationship prose with no
machine-usable RB output.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import personal_relationship_guard as prg
import mutations
import mutation_policy
import employment_state


ADVOCACY_RX = re.compile(
    r"\b(help|assist|support|happy to|glad to|offer|put in|refer|intro|introduc\w*|recommend\w*)\b",
    re.I,
)
SPONSOR_RX = re.compile(
    r"\b(internally|inside|with my|my team|check with|ask around|put in a word|vouch|sponsor)\b",
    re.I,
)
INSIDER_RX = re.compile(
    r"\b(vmo|vendor management|procurement|supplier|corporate|internal|ecosystem|help desk|migration)\b",
    re.I,
)
WARMTH_RX = re.compile(
    r"\b(thinking about you|checking in|how are you|been thinking|hope you|friend|supporter)\b",
    re.I,
)
OPPORTUNITY_RX = re.compile(
    r"\b(opportunit\w*|role|job|company|companies|unisys|harri|mcdonald'?s|opening|search)\b",
    re.I,
)
RECIPROCITY_RX = re.compile(
    r"\b(let me know|anything i can do|how can i help|keep me posted|happy to look)\b",
    re.I,
)
RECRUITING_RX = re.compile(
    r"\b(recruiter|headhunter|hiring|role|position|director|interview|resume|candidate|team|chat with folks)\b",
    re.I,
)
INTERNAL_CIRCULATION_RX = re.compile(
    r"\b(shared your info|shared .* notes|shared .* with the team|notes from last time|with the team|circulat\w*)\b",
    re.I,
)
EARLY_STAGE_RX = re.compile(
    r"\b(just starting|starting to chat|early|will be in touch|i will be in touch)\b",
    re.I,
)

# Recruiting state-transition signals introduced for the RI event intake
# sprint (T-2026-05-19-005 / T-2026-05-19-006). These sit alongside the
# existing warm_recruiting_reengagement / internal_circulation_signal
# detectors — those describe *tone*, these describe *what happened*.
INTERVIEW_COMPLETED_RX = re.compile(
    # Allow up to ~80 chars of intervening text between "interview" and the
    # completion phrase ("went well", "yesterday", etc.) so phrasings like
    # "the interview with Ryan from Global Payments went well yesterday" match.
    r"\b(interview\b[^.!?\n]{0,80}\b(?:went well|was good|happened|today|yesterday|"
    r"earlier this week|wrapped up|was great|was good)"
    r"|had (?:my|the|an|a) interview"
    r"|just (?:finished|wrapped) (?:my|the|an|a) interview"
    r"|finished interviewing|interviewed (?:with|for|yesterday|today))\b",
    re.I,
)
RECRUITER_SCREEN_COMPLETED_RX = re.compile(
    r"\b((?:headhunter|recruiter) (?:i (?:talked|spoke) (?:to|with)|screen|screening)"
    r"|spoke (?:to|with) (?:a|the) (?:headhunter|recruiter)"
    r"|recruiter (?:call|conversation|screen)|did the recruiter screen)\b",
    re.I,
)
RESUME_REQUESTED_RX = re.compile(
    r"\b(asked (?:me )?for (?:my )?(?:resume|cv)"
    r"|requested (?:my )?(?:resume|cv)"
    r"|wanted (?:my )?(?:resume|cv)|send (?:me )?your (?:resume|cv))\b",
    re.I,
)
RESUME_SENT_RX = re.compile(
    r"\b((?:i )?sent (?:my|the|over my) (?:resume|cv)"
    r"|sent the (?:resume|cv) (?:over|to)"
    r"|forwarded (?:my|the) (?:resume|cv))\b",
    re.I,
)
FOLLOWUP_SENT_RX = re.compile(
    r"\b((?:i )?sent (?:the|a|my) (?:follow-?up|followup|reply|thank ?you|note)"
    r"|followed up|replied (?:to|with) (?:the recruiter|the hiring manager|them))\b",
    re.I,
)
SECOND_CONVERSATION_RX = re.compile(
    r"\b(wants? (?:to )?(?:have )?another (?:conversation|chat|call|meeting)"
    r"|second (?:conversation|interview|round|chat|call)"
    r"|talk again (?:this|next) week|follow-?up (?:conversation|call|chat))\b",
    re.I,
)
WAITING_ON_RECRUITER_RX = re.compile(
    r"\b(have not heard back|haven'?t heard back|no response from (?:the )?(?:recruiter|headhunter|hiring manager)"
    r"|silence from (?:the )?(?:recruiter|headhunter)|waiting (?:on|for) (?:the )?(?:recruiter|headhunter))\b",
    re.I,
)
COMMUNICATION_FAILURE_RX = re.compile(
    r"\b("
    r"email(?:s)?\s+(?:bounced|bounce(?:d)?\s+back|failed|not\s+deliver(?:ed|able))"
    r"|bounce(?:d)?\s+back"
    r"|delivery\s+(?:status|failure|failed)"
    r"|undeliver(?:ed|able)"
    r"|couldn'?t\s+(?:reach|email|get\s+through)"
    r"|tried\s+(?:to\s+)?(?:reach|email|send)"
    r"|i\s+(?:sent|emailed).{0,80}(?:bounced|failed|undeliver)"
    r"|dns|nameserver|mx\s+record|email\s+routing|domain\s+(?:issue|outage)"
    r")\b",
    re.I,
)
CHANNEL_ESCALATION_RX = re.compile(
    r"\b("
    r"text(?:ed|ing)?\s+(?:you|me)"
    r"|via\s+(?:text|sms|imessage)"
    r"|tracked\s+(?:you|me)\s+down"
    r"|found\s+(?:you|me)\s+(?:on|via)"
    r"|switch(?:ed|ing)\s+(?:to|channels?)"
    r"|reach(?:ed)?\s+out\s+(?:by|via|on)\s+(?:text|sms|imessage|phone)"
    r")\b",
    re.I,
)
HIGH_INTEREST_RX = re.compile(
    r"\b((?:i'?d be |would be |i am |i'?m )?very interested"
    r"|really want this (?:role|job|position)|high(?:-| )interest)\b",
    re.I,
)
INTRO_GOVERNANCE_RX = re.compile(
    r"\b(intro|introduc\w+|connect (?:you|me|us)|referral|refer|who (?:do|should) you know|"
    r"send (?:you|me|us) (?:someone|names)|make a connection)\b",
    re.I,
)
FOLLOWUP_OBLIGATION_RX = re.compile(
    r"\b(follow up|circle back|check back|send (?:you|me|over)|i'?ll (?:send|review|look|introduce|connect)|"
    r"next step|in (?:a )?(?:few|30|thirty) days|next month)\b",
    re.I,
)
OPERATOR_DOMAIN_RX = re.compile(
    r"\b(franchise|franchisee|franchisor|operator|operations|procurement|deployment|rollout|"
    r"adoption|pos|point of sale|payments|commerce|restaurant|hospitality|hilton|inspire brands|dog haus)\b",
    re.I,
)
PEER_RECIPROCITY_RX = re.compile(
    r"\b(mutual|both|you and i|same language|similar experience|that makes sense|exactly|"
    r"i can help|happy to help|let me see what i can do|good fit)\b",
    re.I,
)
EXTRACTION_RISK_RX = re.compile(
    r"\b(pick your brain|free advice|can you give me|who can you introduce|any intros|"
    r"help me source|your network)\b",
    re.I,
)

COMPANY_HINTS = (
    "Coates Group", "Global Payments", "Genius", "Harri", "Unisys",
    "McDonald's", "Toast", "Maho", "Matrix Software Solutions",
)

# RB-DEFECT-2026-09-18 (Sal Nazir call-note incident): explicitly supplied
# contact fields -- at minimum phone, email, current/former organization,
# and role -- were never extracted from manual RI text at all (phone/email
# were hardcoded None everywhere in this file; "PhoneN can be reached at
# 416-457-7269" was preserved nowhere but the raw source_text_snippet).
# North American phone format, requiring a separator between groups (avoids
# matching an unrelated bare 10-digit id/number that happens to appear in
# text with no formatting).
_PHONE_RX = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# "has been away from PAR since May 2026" / "left PAR Technology in May 2026"
# -- a dated departure statement. Best-effort, same regex-heuristic style as
# _infer_company/_infer_opportunity elsewhere in this file; a miss just means
# the departure gets recorded without a precise end date (still correctly
# not treated as current employment -- see _resolve_departure below).
_DEPARTURE_SINCE_RX = re.compile(
    r"(?:away from|left|departed)\s+[A-Za-z0-9 &.'-]+?\s+(?:since|in)\s+"
    r"([A-Za-z]+\s+\d{4}|\d{4}-\d{2})",
    re.I,
)
_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Signal types that only indicate the text touched relevant industry/company
# vocabulary or a bare topical mention -- NOT that a grounded ask,
# commitment, next step, or genuine opportunity movement occurred. Present
# alone (or together with each other and nothing else), these must not be
# sufficient to invent an opportunity/thread/loop/recruiter narrative --
# confirmed live 2026-09-18: a phone call that was purely a relationship
# touch + M&A intelligence observation (operator_domain_overlap fired only
# because the conversation mentioned "restaurant"/"payments" in describing
# PAR Technology's business) produced a fabricated "warm recruiting
# re-engagement" thread and loop. Paired with a genuinely grounded signal
# (e.g. reciprocal_peer_signal, the existing strategic_peer_exploration
# case) they still count -- this only blocks the WEAK-ONLY case.
_WEAK_ONLY_OPPORTUNITY_TYPES = {"operator_domain_overlap", "opportunity_signal"}


def _extract_contact_fields(text: str) -> dict:
    """Best-effort extraction of explicitly stated contact fields from free
    text. Returns {"phone": str|None, "email": str|None}."""
    phone_m = _PHONE_RX.search(text)
    email_m = _EMAIL_RX.search(text)
    return {
        "phone": phone_m.group(0).strip() if phone_m else None,
        "email": email_m.group(0).strip() if email_m else None,
    }


def _extract_departure_since(text: str) -> str | None:
    """Best-effort ISO (YYYY-MM-01) departure date from 'since <Month Year>'
    / 'in <Month Year>' phrasing. Day is fixed at 01 -- prose never supplies
    a day, same convention as employment_state._parse_end_date."""
    m = _DEPARTURE_SINCE_RX.search(text)
    if not m:
        return None
    raw = m.group(1)
    if re.match(r"^\d{4}-\d{2}$", raw):
        return f"{raw}-01"
    parts = raw.split()
    if len(parts) == 2:
        month = _MONTH_NUMBERS.get(parts[0][:3].lower())
        if month:
            return f"{parts[1]}-{month:02d}-01"
    return None


def _is_former_employer_label(company: str | None) -> str | None:
    """Return the bare company name if `company` reads as an explicit
    former-employer label ("Formerly PAR Technology" / "Former PAR
    Technology"), else None. The manual RI API's `organization` param is
    already caller-supplied structured text (not free-text inference) --
    when a caller marks it "Formerly X", that's an explicit, grounded
    statement worth routing through employment-history resolution, not a
    guess this module has to parse out of prose itself."""
    if not company:
        return None
    m = re.match(r"^\s*former(?:ly)?\s+(.+)$", company, flags=re.I)
    return m.group(1).strip() if m else None


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(value[:10])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _resolve_contact(baseline: list[dict], *, contact_id: str | None, name: str | None) -> dict | None:
    if contact_id:
        return next((e for e in baseline if e.get("id") == contact_id), None)
    if name:
        slug = _slug(name)
        return next(
            (
                e for e in baseline
                if e.get("id") == slug or (e.get("name") or "").lower() == name.lower()
            ),
            None,
        )
    return None


def _signal(label: str, strength: float, evidence: list[str], rationale: str) -> dict:
    return {
        "type": label,
        "strength": round(min(max(strength, 0.0), 1.0), 3),
        "evidence": evidence[:5],
        "rationale": rationale,
        "grounding": "manual_user_provided",
    }


def _hits(rx: re.Pattern, text: str, labels: list[str]) -> list[str]:
    found = []
    for label in labels:
        if re.search(r"\b" + re.escape(label) + r"\b", text, flags=re.I):
            found.append(label)
    if not found and rx.search(text):
        found.append(rx.search(text).group(0))
    return found[:5]


def _infer_company(text: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for company in COMPANY_HINTS:
        if re.search(re.escape(company), text, flags=re.I):
            return company
    m = re.search(r"\b([A-Z][A-Za-z&.'-]+(?:\s+[A-Z][A-Za-z&.'-]+){0,2})\s+(?:role|opportunity|team)\b", text)
    return m.group(1) if m else None


def _infer_opportunity(text: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    patterns = [
        r"\b(Director,\s*McDonald'?s\s+Global\s+Account(?:\s+role)?)\b",
        r"\b(Director\s+of\s+McDonald'?s\s+Global\s+Account(?:\s+role)?)\b",
        r"\b([A-Z][A-Za-z,\s]+Global\s+Account\s+role)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def _thread_id_for(*, event_at: date, company: str | None, opportunity: str | None) -> str:
    bits = [event_at.strftime("%Y-%m"), company or "opportunity", opportunity or "recruiting"]
    slug = _slug("-".join(bits))
    return "T-" + slug[:80]


def _detect_signals(text: str) -> list[dict]:
    signals: list[dict] = []

    warmth = _hits(WARMTH_RX, text, ["thinking about you", "checking in", "supporter", "friend"])
    if warmth:
        signals.append(_signal(
            "relationship_warmth",
            0.78,
            warmth,
            "Direct emotional check-in or personal support language.",
        ))

    advocacy = _hits(ADVOCACY_RX, text, ["help", "assist", "support", "recommendations", "offer"])
    if advocacy:
        signals.append(_signal(
            "advocacy_signal",
            0.84,
            advocacy,
            "Contact moved beyond passive encouragement into help-oriented behavior.",
        ))

    sponsor = _hits(SPONSOR_RX, text, ["internally", "put in a word", "vouch", "ask around"])
    if sponsor:
        signals.append(_signal(
            "sponsorship_signal",
            0.80,
            sponsor,
            "Evidence suggests the contact may act inside an organization or access path.",
        ))

    insider = _hits(INSIDER_RX, text, ["VMO", "vendor management", "supplier", "corporate", "help desk", "migration"])
    if insider:
        signals.append(_signal(
            "insider_access_signal",
            0.82,
            insider,
            "Conversation includes operational or internal ecosystem access.",
        ))

    opportunity = _hits(OPPORTUNITY_RX, text, ["Unisys", "Harri", "McDonald's", "opportunity", "job"])
    if opportunity:
        signals.append(_signal(
            "opportunity_signal",
            0.76,
            opportunity,
            "Conversation names opportunity targets, companies, or job-search movement.",
        ))

    reciprocity = _hits(RECIPROCITY_RX, text, ["let me know", "keep me posted", "happy to look"])
    if reciprocity:
        signals.append(_signal(
            "reciprocity_indicator",
            0.70,
            reciprocity,
            "Contact left the door open for continued exchange or follow-through.",
        ))

    recruiting = _hits(RECRUITING_RX, text, ["recruiter", "hiring", "role", "position", "team", "chat with folks"])
    if recruiting:
        signals.append(_signal(
            "warm_recruiting_reengagement",
            0.74,
            recruiting,
            "Recruiting conversation is active enough to warrant opportunity-state tracking.",
        ))

    circulation = _hits(
        INTERNAL_CIRCULATION_RX,
        text,
        ["shared your info", "notes from last time", "with the team"],
    )
    if circulation:
        signals.append(_signal(
            "internal_circulation_signal",
            0.86,
            circulation,
            "Recruiter confirmed Todd's background or prior notes moved to internal stakeholders.",
        ))

    early = _hits(EARLY_STAGE_RX, text, ["just starting", "starting to chat", "will be in touch"])
    if early:
        signals.append(_signal(
            "early_stage_recruiting_signal",
            0.60,
            early,
            "Language indicates the opportunity is live but still early in process.",
        ))

    # Recruiting state transitions — what *happened*, not how it felt.
    # These power the recruiting workflow per RI_EVENT_INTAKE_DESIGN.md and
    # T-2026-05-19-006 (Ryan Hildebrand interview, Simin/Hari screen).

    interview = _hits(
        INTERVIEW_COMPLETED_RX, text,
        ["interview went well", "interview yesterday", "had my interview", "finished interviewing"],
    )
    if interview:
        signals.append(_signal(
            "interview_completed",
            0.90,
            interview,
            "Interview event occurred; opportunity should advance to post-interview state.",
        ))

    screen = _hits(
        RECRUITER_SCREEN_COMPLETED_RX, text,
        ["headhunter I talked to", "spoke to a headhunter", "recruiter screen", "recruiter call"],
    )
    if screen and "interview_completed" not in {s["type"] for s in signals}:
        signals.append(_signal(
            "recruiter_screen_completed",
            0.86,
            screen,
            "Recruiter screen took place; opportunity has begun, awaiting next-step direction.",
        ))

    resume_req = _hits(
        RESUME_REQUESTED_RX, text,
        ["asked for my resume", "requested my resume", "wanted my resume"],
    )
    if resume_req:
        signals.append(_signal(
            "resume_requested",
            0.88,
            resume_req,
            "Resume was requested — open a send-resume loop unless already sent in the same artifact.",
        ))

    resume_sent = _hits(
        RESUME_SENT_RX, text,
        ["sent my resume", "sent the resume over", "forwarded my resume"],
    )
    if resume_sent:
        signals.append(_signal(
            "resume_sent",
            0.90,
            resume_sent,
            "Resume was sent — close any open send-resume loop, open a recruiter-followup loop.",
        ))

    followup = _hits(
        FOLLOWUP_SENT_RX, text,
        ["sent the follow-up", "sent the followup", "followed up", "sent a thank you"],
    )
    if followup:
        signals.append(_signal(
            "followup_sent",
            0.82,
            followup,
            "Operator already completed the follow-up — close obligation loop, open response-waited loop.",
        ))

    second_conv = _hits(
        SECOND_CONVERSATION_RX, text,
        ["another conversation", "second conversation", "follow-up call", "talk again next week"],
    )
    if second_conv:
        signals.append(_signal(
            "second_conversation_requested",
            0.84,
            second_conv,
            "A next conversation was explicitly requested — open scheduling loop with target this/next week.",
        ))

    waiting = _hits(
        WAITING_ON_RECRUITER_RX, text,
        ["have not heard back", "haven't heard back", "no response from", "waiting on the recruiter"],
    )
    if waiting:
        signals.append(_signal(
            "waiting_on_recruiter",
            0.72,
            waiting,
            "Recruiter silence flagged — open a light-touch follow-up loop with 5-7 day target.",
        ))

    comm_failure = _hits(
        COMMUNICATION_FAILURE_RX, text,
        ["bounced", "bounce back", "delivery failure", "undeliverable", "DNS", "nameserver", "email routing"],
    )
    if comm_failure:
        signals.append(_signal(
            "communication_failure_risk",
            0.88,
            comm_failure,
            "Professional communication failed; relationship or opportunity continuity may be at risk.",
        ))

    channel_escalation = _hits(
        CHANNEL_ESCALATION_RX, text,
        ["texted you", "via text", "tracked me down", "switched channels", "reached out by text"],
    )
    if channel_escalation:
        signals.append(_signal(
            "multi_channel_escalation",
            0.86,
            channel_escalation,
            "Counterparty switched channels after friction, indicating persistence and above-average engagement.",
        ))

    interest = _hits(
        HIGH_INTEREST_RX, text,
        ["very interested", "really want this role", "high interest"],
    )
    if interest:
        signals.append(_signal(
            "high_interest_role",
            0.80,
            interest,
            "Operator explicitly stated high interest in the role — boost opportunity thread urgency.",
        ))

    intro = _hits(
        INTRO_GOVERNANCE_RX, text,
        ["intro", "introduce", "referral", "connect you", "make a connection"],
    )
    if intro:
        signals.append(_signal(
            "intro_governance_needed",
            0.78,
            intro,
            "Conversation raised potential introductions; network equity should be protected with trust and fit checks.",
        ))

    followup = _hits(
        FOLLOWUP_OBLIGATION_RX, text,
        ["follow up", "circle back", "check back", "I'll send", "next step", "30 days"],
    )
    if followup:
        signals.append(_signal(
            "followup_loop_candidate",
            0.82,
            followup,
            "Conversation contains a relationship or opportunity follow-up obligation worth loop review.",
        ))

    operator_domain = _hits(
        OPERATOR_DOMAIN_RX, text,
        ["franchise", "operator", "procurement", "deployment", "POS", "restaurant", "Dog Haus"],
    )
    if operator_domain:
        signals.append(_signal(
            "operator_domain_overlap",
            0.80,
            operator_domain,
            "Conversation touches Todd's restaurant/franchise/operator execution domain.",
        ))

    peer = _hits(
        PEER_RECIPROCITY_RX, text,
        ["mutual", "same language", "similar experience", "happy to help", "good fit"],
    )
    if peer:
        signals.append(_signal(
            "reciprocal_peer_signal",
            0.76,
            peer,
            "Language suggests peer-level exchange rather than one-way extraction.",
        ))

    extraction = _hits(
        EXTRACTION_RISK_RX, text,
        ["pick your brain", "free advice", "any intros", "your network"],
    )
    if extraction:
        signals.append(_signal(
            "network_extraction_risk",
            0.74,
            extraction,
            "Conversation may be asking Todd to deploy advice or network equity before trust/reciprocity is proven.",
        ))

    return signals


def _dormancy_risk(entry: dict | None, as_of: date, event_at: date) -> dict:
    if not entry:
        return {"before": None, "after": None, "days_since_touch_before": None}
    last_touch = entry.get("last_touch")
    if not last_touch:
        before_days = None
        before = "unknown"
    else:
        before_days = (as_of - date.fromisoformat(last_touch)).days
        tier = entry.get("rc_tier")
        threshold = core.TIER_THRESHOLD_DAYS.get(tier, 90)
        before = "high" if before_days > threshold else "medium" if before_days > threshold * 0.7 else "low"
    after_days = max(0, (as_of - event_at).days)
    after = "low" if after_days <= 14 else "medium" if after_days <= 45 else "high"
    return {
        "before": before,
        "after": after,
        "days_since_touch_before": before_days,
        "days_since_touch_after": after_days,
    }


def _metric_pack(entry: dict | None, signals: list[dict], *, as_of: date, event_at: date) -> dict:
    strengths = {s["type"]: s["strength"] for s in signals}
    tier_bonus = {
        ("RC", "inner"): 0.18,
        ("RC", "broader"): 0.12,
        ("RC", "dormant_valuable"): 0.08,
        ("LKI", None): 0.04,
    }.get(((entry or {}).get("signal_class"), (entry or {}).get("rc_tier")), 0.0)

    advocacy = min(0.98, strengths.get("advocacy_signal", 0.0) * 0.75 + tier_bonus + strengths.get("reciprocity_indicator", 0.0) * 0.10)
    sponsor = min(0.95, strengths.get("sponsorship_signal", 0.0) * 0.65 + strengths.get("insider_access_signal", 0.0) * 0.20 + tier_bonus)
    soft_advocacy = strengths.get("internal_circulation_signal", 0.0) * 0.65 + strengths.get("warm_recruiting_reengagement", 0.0) * 0.15
    advocacy = min(0.98, advocacy + soft_advocacy)
    trust = min(0.99, 0.35 + tier_bonus + strengths.get("relationship_warmth", 0.0) * 0.22 + advocacy * 0.20 + sponsor * 0.12)
    strategic = min(0.99, strengths.get("opportunity_signal", 0.0) * 0.35 + strengths.get("insider_access_signal", 0.0) * 0.25 + strengths.get("warm_recruiting_reengagement", 0.0) * 0.25 + strengths.get("internal_circulation_signal", 0.0) * 0.30 + strengths.get("operator_domain_overlap", 0.0) * 0.32 + strengths.get("intro_governance_needed", 0.0) * 0.20 + advocacy * 0.20 + sponsor * 0.20)
    momentum = min(0.99, 0.20 + strengths.get("relationship_warmth", 0.0) * 0.25 + strengths.get("reciprocity_indicator", 0.0) * 0.20 + strengths.get("reciprocal_peer_signal", 0.0) * 0.18 + strengths.get("followup_loop_candidate", 0.0) * 0.16 + strengths.get("internal_circulation_signal", 0.0) * 0.25 + advocacy * 0.20)
    dormancy = _dormancy_risk(entry, as_of, event_at)

    return {
        "trust_score": round(trust * 100, 1),
        "advocacy_probability": round(advocacy, 3),
        "sponsor_probability": round(sponsor, 3),
        "soft_internal_advocate_probability": round(min(0.95, soft_advocacy), 3),
        "strategic_value": round(strategic * 100, 1),
        "relationship_momentum": round(momentum * 100, 1),
        "dormancy_risk": dormancy,
    }


_OPPORTUNITY_TRIGGER_TYPES = {
    "warm_recruiting_reengagement", "internal_circulation_signal", "opportunity_signal",
    "operator_domain_overlap", "intro_governance_needed", "communication_failure_risk",
    "multi_channel_escalation",
}
_OPPORTUNITY_STRONG_TRIGGER_TYPES = _OPPORTUNITY_TRIGGER_TYPES - _WEAK_ONLY_OPPORTUNITY_TYPES


def _opportunity_state(signals: list[dict], *, company: str | None, opportunity: str | None) -> dict | None:
    types = {s["type"] for s in signals}
    # RB-DEFECT-2026-09-18: operator_domain_overlap/opportunity_signal alone
    # only mean the text touched relevant vocabulary or named a company --
    # not a grounded ask/commitment/next step. This must invent an
    # opportunity narrative only when a genuinely grounded trigger type is
    # present (the strong set), OR the specific weak+reciprocal_peer_signal
    # combo below (strategic_peer_exploration) applies. Any OTHER signal
    # (e.g. followup_loop_candidate, handled independently in
    # _proposed_mutations -- see acceptance test 17) does NOT itself confer
    # grounding on a weak trigger; only intersecting against the trigger set
    # itself, not the full detected signal set, avoids that false pairing.
    has_strong_trigger = bool(types & _OPPORTUNITY_STRONG_TRIGGER_TYPES)
    has_grounded_weak_combo = "operator_domain_overlap" in types and "reciprocal_peer_signal" in types
    if not has_strong_trigger and not has_grounded_weak_combo:
        return None
    communication_risk = "communication_failure_risk" in types
    channel_escalation = "multi_channel_escalation" in types
    if communication_risk and channel_escalation:
        stage = "active_opportunity_communication_risk"
        confidence = 0.84
    elif communication_risk:
        stage = "communication_risk_detected"
        confidence = 0.78
    elif "internal_circulation_signal" in types:
        stage = "active_internal_evaluation"
        confidence = 0.82
    elif "warm_recruiting_reengagement" in types:
        stage = "warm_recruiting_reengagement"
        confidence = 0.68
    elif "operator_domain_overlap" in types and "reciprocal_peer_signal" in types:
        stage = "strategic_peer_exploration"
        confidence = 0.72
    elif "intro_governance_needed" in types:
        stage = "intro_fit_evaluation"
        confidence = 0.66
    else:
        stage = "opportunity_identified"
        confidence = 0.55
    is_recruiting = stage in {"active_internal_evaluation", "warm_recruiting_reengagement"}
    return {
        "organization": company,
        "opportunity": opportunity,
        "stage": stage,
        "prior_stage": "passive_reconnect_or_unknown",
        "confidence": round(confidence, 2),
        "momentum": (
            "elevated_positive_with_operational_risk"
            if stage == "active_opportunity_communication_risk" else
            "moderate_positive" if stage in {"active_internal_evaluation", "strategic_peer_exploration"} else
            "early_positive"
        ),
        "recommended_posture": (
            "act_today_restore_channel_and_acknowledge_persistence"
            if communication_risk else
            "active_intro_evaluation_with_trust_and_fit_checks"
            if stage == "intro_fit_evaluation" else
            "active_strategic_peer_nurture"
            if stage == "strategic_peer_exploration" else
            "light_touch_no_chase_5_to_7_business_days"
            if is_recruiting else
            # RB-DEFECT-2026-09-18: this used to be the same recruiting-
            # flavored text regardless of whether any recruiting signal
            # existed -- confirmed live, a pure relationship/intelligence
            # call got "await recruiter/team follow-up" with zero recruiting
            # context. Only the genuinely recruiting-classified stages above
            # get recruiting language now; everything else gets a neutral
            # monitor posture.
            "light_touch_monitor_5_to_7_business_days"
        ),
        "next_expected_action": (
            "repair email channel, respond through the successful channel, and preserve opportunity continuity"
            if communication_risk else
            "score introduction candidates and protect network equity"
            if stage == "intro_fit_evaluation" else
            "capture meeting outcome, evaluate follow-up loops, and maintain peer cadence"
            if stage == "strategic_peer_exploration" else
            "await recruiter/team follow-up"
            if is_recruiting else
            "monitor for further developments; no recruiting or sales action implied"
        ),
        "inferred_internal_stakeholder_circulation": "internal_circulation_signal" in types,
        "communication_risk_flag": communication_risk,
        "multi_channel_escalation": channel_escalation,
    }


def _proposed_mutations(
    entry: dict | None,
    signals: list[dict],
    *,
    event_at: date,
    captured_at: date,
    name: str | None,
    company: str | None,
    opportunity: str | None,
    phone: str | None = None,
    email: str | None = None,
    former_employer_label: str | None = None,
    departure_since: str | None = None,
) -> list[dict]:
    out: list[dict] = []
    signal_types = {s["type"] for s in signals}
    contact_id = entry.get("id") if entry else (_slug(name) if name else None)
    is_recruiting_signal = bool({"warm_recruiting_reengagement", "internal_circulation_signal", "interview_completed", "recruiter_screen_completed"} & signal_types)

    if not entry and name:
        out.append({
            "operation": "addContact",
            "safe_to_write": True,
            "endpoint": "POST /contacts",
            "body": {
                "id": contact_id,
                "name": name,
                "signal_class": "LKI" if "internal_circulation_signal" in signal_types else "LMI",
                "company": company,
                "role": "Recruiting / talent contact" if is_recruiting_signal else "Relationship contact",
                # RB-DEFECT-2026-09-18: explicitly supplied phone/email used
                # to be dropped even for brand-new contacts -- apply_mutations()
                # hardcoded both to None regardless of what was extracted.
                "phone": phone,
                "email": email,
                "last_touch": event_at.isoformat(),
                "source": "manual_relationship_intake",
                "notes": (
                    f"[{event_at.isoformat()}] Manual RI: "
                    f"{company or 'Unknown organization'} / {opportunity or 'unknown opportunity'}. "
                    "Relationship signal captured with review-first proposed mutations."
                ),
            },
            "reason": "Recruiter/contact is not in baseline; add as a relationship-intelligence contact before tracking the opportunity.",
        })

    if entry:
        cid = entry["id"]
        current_lt = entry.get("last_touch")
    else:
        cid = contact_id
        current_lt = None

    if cid and (not current_lt or event_at.isoformat() > current_lt):
        out.append({
            "operation": "touchContact",
            "safe_to_write": bool(entry),
            "endpoint": "POST /touch",
            "body": {
                "id": cid,
                "date": event_at.isoformat(),
                "source": "manual_relationship_intake",
            },
            "reason": (
                "Manual artifact contains direct interaction newer than recorded last_touch."
                if entry else
                "Satisfied by addContact.last_touch for new contacts; no separate touch needed until contact exists."
            ),
        })

    # RB-DEFECT-2026-09-18: canonical contact-field enrichment for an
    # EXISTING contact. Each explicitly supplied field is routed through the
    # shared mutation_policy decision engine (system/scripts/mutation_policy.py)
    # instead of being silently dropped (phone/email) or silently ignored
    # (a dated former-employer statement, which used to leave current_company
    # pointing at an employer the contact had already left). safe_to_write
    # mirrors decision.auto_apply exactly -- apply_mutations() recomputes the
    # same decision at write time (see there) to record a durable receipt
    # only once the write is known to have actually happened.
    if entry and cid:
        for field_name, new_value in (("phone", phone), ("email", email)):
            if not new_value:
                continue
            decision = mutation_policy.decide(
                source="manual_relationship_intake",
                new_value=new_value,
                existing_value=entry.get(field_name),
                is_replacement=True,
                field_name=field_name,
                entity_id=cid,
                observed_at=captured_at.isoformat(),
            )
            if decision.status == mutation_policy.REJECTED_DUPLICATE:
                continue
            out.append({
                "operation": "updateContact",
                "safe_to_write": decision.auto_apply,
                "endpoint": "POST /contacts/update",
                "body": {"id": cid, field_name: new_value},
                "reason": decision.reason,
                "decision_class": decision.status,
            })

        if former_employer_label:
            departure = employment_state.resolve_operator_confirmed_departure(
                entry, former_company=former_employer_label,
                departed_since=departure_since,
            )
            if departure:
                out.append({
                    "operation": "updateEmploymentState",
                    "safe_to_write": True,
                    "endpoint": "POST /contacts/update",
                    "body": {"id": cid, **departure},
                    "reason": (
                        f"Operator-confirmed departure from {former_employer_label} "
                        f"(dated {departure_since})" if departure_since else
                        f"Operator-confirmed departure from {former_employer_label}"
                    ) + " -- preserves employment history, does not treat the former employer as current.",
                    "decision_class": mutation_policy.AUTO_ADDED_DATED_SUCCESSOR
                        if departure_since else mutation_policy.AUTO_ADDED_NET_NEW,
                })

    opp_state = _opportunity_state(signals, company=company, opportunity=opportunity)

    tags = []
    if company and "mcdonald" in (opportunity or "").lower():
        tags.append("mcdonalds_ecosystem")
    if "insider_access_signal" in signal_types:
        tags.append("insider_access")
    if "advocacy_signal" in signal_types or "internal_circulation_signal" in signal_types:
        tags.append("advocacy_observed")
    if "sponsorship_signal" in signal_types:
        tags.append("sponsor_candidate")
    if "internal_circulation_signal" in signal_types:
        tags.extend(["soft_internal_advocate", "internal_circulation"])
    # RB-DEFECT-2026-09-18: operator_domain_overlap alone (bare industry
    # vocabulary -- "restaurant"/"payments"/"operator" appearing anywhere in
    # the text) used to tag the contact restaurant_operator_overlap/
    # franchise_governance_signal unconditionally -- confirmed live, not
    # grounded in anything the call actually established about Sal Nazir's
    # relationship to franchise governance specifically. Only tag when
    # _opportunity_state() independently judged the signal set genuinely
    # grounded (opp_state is not None -- see its own weak-signal gate above).
    if "operator_domain_overlap" in signal_types and opp_state:
        tags.extend(["restaurant_operator_overlap", "franchise_governance_signal"])
    if "reciprocal_peer_signal" in signal_types:
        tags.append("reciprocal_peer")
    if "intro_governance_needed" in signal_types:
        tags.append("intro_governance_review")
    if "network_extraction_risk" in signal_types:
        tags.append("network_equity_protection")
    if "communication_failure_risk" in signal_types:
        tags.extend(["communication_risk", "email_infrastructure_risk"])
    if "multi_channel_escalation" in signal_types:
        tags.append("multi_channel_engagement")
    if tags:
        out.append({
            "operation": "card_or_baseline_tag_update",
            "safe_to_write": False,
            "endpoint": None,
            "body": {"id": cid, "tags": sorted(set(tags))},
            "reason": "No dedicated tag mutation endpoint exists yet; caller should route through a review-safe tag/card writer.",
        })

    if opp_state:
        is_recruiting = opp_state.get("stage") in {
            "active_internal_evaluation",
            "warm_recruiting_reengagement",
        }
        thread_id = _thread_id_for(
            event_at=event_at,
            company=company,
            opportunity=opportunity or ("strategic relationship" if not is_recruiting else None),
        )
        out.append({
            "operation": "openThread",
            "safe_to_write": True,
            "endpoint": "POST /threads",
            "body": {
                "id": thread_id,
                "title": (
                    f"{company or 'Strategic relationship'} / {opportunity or 'strategic relationship'}"
                    if not is_recruiting else
                    f"{company or 'Recruiting'} / {opportunity or 'recruiting opportunity'}"
                ),
                "type": "job_opportunity" if is_recruiting else "relationship_opportunity",
                "people": [cid] if cid else [],
                "companies": [c for c in [company, "McDonald's" if opportunity and "mcdonald" in opportunity.lower() else None] if c],
                "context": (
                    f"Manual RI captured {event_at.isoformat()}: "
                    f"{name or 'Counterparty'} created a {opp_state.get('stage')} signal."
                ),
                "current_state": (
                    "Opportunity state: active with elevated engagement confidence and communication-risk flag. "
                    "Evidence: counterparty persisted through failed email delivery and switched channels; "
                    "posture is act today, restore email continuity, and acknowledge the extra effort."
                    if "communication_failure_risk" in signal_types else
                    "Opportunity state: active internal evaluation. Evidence: recruiter shared Todd's info "
                    "and prior notes with the team; posture is light-touch, await next inbound movement."
                    if "internal_circulation_signal" in signal_types else
                    "Relationship state: strategic peer exploration. Evidence: reciprocal operator/domain overlap; "
                    "posture is active nurture with concrete follow-up and intro governance."
                    if opp_state.get("stage") == "strategic_peer_exploration" else
                    "Opportunity state: warm recruiting re-engagement; await next recruiter movement."
                    if is_recruiting else
                    # RB-DEFECT-2026-09-18: same recruiting-flavored-by-default
                    # bug as _opportunity_state()'s own fallback text -- fixed
                    # the same way, only here for the rare remaining combo
                    # that reaches this generic branch without being recruiting.
                    f"Opportunity state: {opp_state.get('stage')}. {opp_state.get('next_expected_action', 'monitor for further developments')}."
                ),
                "target_close": (event_at + timedelta(days=45)).isoformat(),
                "boost_for_brief": "high" if "internal_circulation_signal" in signal_types else "medium",
                "boost_score": 1.3 if "internal_circulation_signal" in signal_types else 1.2,
            },
            "reason": (
                "Recruiting opportunity has enough movement to become an active strategic thread."
                if is_recruiting else
                "Strategic relationship signal has enough movement to become an active thread."
            ),
        })

        target = (event_at + timedelta(days=7)).isoformat()
        out.append({
            "operation": "loopAdd",
            "safe_to_write": True,
            "endpoint": "POST /loops",
            "body": {
                "party": name or (entry.get("name") if entry else company or "Recruiting opportunity"),
                "description": (
                    f"Protect {company or 'active opportunity'} continuity after communication failure. "
                    "Verify email restoration, reply through the successful channel, and acknowledge the bounced-email issue."
                    if "communication_failure_risk" in signal_types else
                    f"Monitor {company or 'recruiter'} / {opportunity or 'opportunity'} response aging. "
                    "No chase for 5-7 business days after Todd's reply; follow up only if silence persists."
                    if is_recruiting else
                    f"Maintain strategic peer cadence for {company or name or 'this relationship'}; "
                    "capture concrete next step and avoid leaving the opportunity as prose-only context."
                ),
                "target": target,
                "opened": captured_at.isoformat(),
            },
            "reason": (
                "Recruiting re-engagement should create a monitoring loop instead of ad hoc memory."
                if is_recruiting else
                "Strategic relationship movement should create a cadence loop instead of ad hoc memory."
            ),
        })

        if "intro_governance_needed" in signal_types:
            out.append({
                "operation": "loopAdd",
                "safe_to_write": True,
                "endpoint": "POST /loops",
                "body": {
                    "party": name or (entry.get("name") if entry else company or "Intro candidate"),
                    "description": (
                        f"Evaluate intro candidates for {company or 'the conversation'} with fit, trust, "
                        "reciprocity, and network-protection checks before making any introduction."
                    ),
                    "target": (event_at + timedelta(days=3)).isoformat(),
                    "opened": captured_at.isoformat(),
                },
                "reason": "Introduction surface area was detected; RB should propose governance before deploying Todd's network.",
            })

    # RB-DEFECT-2026-09-18 (acceptance test 17): a grounded commitment must
    # be able to create a loop on its own -- this used to be nested inside
    # `if opp_state:`, so a call containing ONLY follow-up language (no
    # recruiting/communication-risk/internal-circulation signal alongside
    # it) never reached this branch at all, even though "I'll send you the
    # intro next week" is exactly the kind of grounded, dated commitment a
    # loop should exist for. The description now carries the actual matched
    # evidence phrase, not a generic "review post-meeting follow-up" label,
    # so the exact commitment is preserved, not paraphrased into genericness.
    followup_signal = next((s for s in signals if s["type"] == "followup_loop_candidate"), None)
    if followup_signal:
        evidence = ", ".join(followup_signal.get("evidence") or []) or "a follow-up commitment"
        out.append({
            "operation": "loopAdd",
            "safe_to_write": True,
            "endpoint": "POST /loops",
            "body": {
                "party": name or (entry.get("name") if entry else company or "Meeting follow-up"),
                "description": (
                    f"Follow up with {name or (entry.get('name') if entry else company) or 'contact'} "
                    f"on: \"{evidence}\" (from the {event_at.isoformat()} conversation)."
                ),
                "target": (event_at + timedelta(days=2)).isoformat(),
                "opened": captured_at.isoformat(),
            },
            "reason": "Transcript contains an explicit follow-up commitment; create a relationship-governance loop instead of relying on memory.",
        })

    # RB-DEFECT-2026-09-18: durable evidence (last touch, channel, contact
    # completeness, intelligence provenance) must be recorded even when no
    # opportunity was warranted -- "update ... without inventing a sales/
    # recruiting opportunity" is the exact required behavior. Fires whenever
    # ANY signal was detected, not only the (now correctly narrower) set
    # that also justifies an opportunity thread/loop.
    if signals:
        out.append({
            "operation": "writeInteractionBrief",
            "safe_to_write": True,
            "endpoint": "internal:briefs",
            "body": {
                "path": f"system/briefs/{event_at.isoformat()}-{contact_id or _slug(name or company or 'manual-ri')}-manual-ri.md",
                "person": contact_id or cid,
                "date": event_at.isoformat(),
                "summary": (
                    f"{name or 'Counterparty'} at {company or 'unknown organization'} produced "
                    f"a {opp_state.get('stage')} relationship signal."
                    if opp_state else
                    f"{name or 'Counterparty'} at {company or 'unknown organization'} produced "
                    f"a relationship touch with {', '.join(sorted(signal_types)) or 'no further'} signal(s); "
                    "no opportunity, thread, or loop was warranted."
                ),
                "signal_implication": opp_state,
            },
            "reason": "Append-only interaction brief makes the RI durable and future-brief addressable.",
        })
    return out


def _write_interaction_brief(path: Path, *, entry_id: str | None, name: str | None,
                             event_at: date, company: str | None,
                             opportunity: str | None, signals: list[dict],
                             opportunity_state: dict | None) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return {"ok": True, "path": str(path), "status": "already_exists"}
    signal_lines = "\n".join(
        f"- {s['type']} ({s['strength']}): {s['rationale']}" for s in signals
    )
    state = json.dumps(opportunity_state or {}, indent=2)
    subject = opportunity or company or "relationship signal"
    text = f"""---
id: {path.stem}
person: {entry_id or ''}
date: {event_at.isoformat()}
channel: manual_artifact
direction: inbound
substance: medium
artifact: manual_relationship_intake
---

# {event_at.isoformat()} — {name or 'Relationship contact'} / {company or 'Unknown organization'}

## Summary
Manual RI intake for {subject}.

## Signal implication
{signal_lines}

## Opportunity state
```json
{state}
```

## Persistence note
Captured by `manual_relationship_intake.py`. This brief is append-only evidence;
baseline, active-thread, and loop mutations are recorded separately when applied.
"""
    path.write_text(text)
    return {"ok": True, "path": str(path), "status": "written"}


def apply_mutations(report: dict) -> dict:
    """Apply safe proposed mutations. Used only when caller explicitly sets
    `apply=True` / `--apply`; the default assess path remains read-only."""
    applied: list[dict] = []
    skipped: list[dict] = []
    entry_id = report["rb_match"].get("id")
    name = report["rb_match"].get("name")
    event_at = _parse_date(report.get("event_at"))
    company = (report.get("opportunity_state") or {}).get("organization")
    opportunity = (report.get("opportunity_state") or {}).get("opportunity")

    for m in report.get("proposed_mutations") or []:
        if not m.get("safe_to_write"):
            skipped.append({"operation": m.get("operation"), "reason": "not_safe_to_write", "body": m.get("body")})
            continue
        op = m.get("operation")
        body = m.get("body") or {}
        try:
            if op == "addContact":
                baseline = core.load_baseline()
                if any(e.get("id") == body["id"] for e in baseline):
                    applied.append({"operation": op, "status": "already_exists", "id": body["id"]})
                else:
                    rc = mutations.cmd_contact_add(type("Ns", (), {
                        "id": body["id"], "name": body["name"], "company": body.get("company"),
                        "role": body.get("role"), "linkedin": None,
                        # RB-DEFECT-2026-09-18: these were hardcoded None
                        # regardless of what _proposed_mutations() actually
                        # extracted and put in body -- an explicitly supplied
                        # phone/email for a brand-new contact was silently
                        # dropped even when the caller passed apply=True.
                        "email": body.get("email"), "phone": body.get("phone"),
                        "last_touch": body.get("last_touch"), "signal_class": body.get("signal_class"),
                        "rc_tier": None, "source": body.get("source"), "notes": body.get("notes"),
                        "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("contact-add failed")
                    entry_id = body["id"]
                    name = body["name"]
                    applied.append({"operation": op, "status": "written", "id": body["id"]})
            elif op == "touchContact":
                if body["id"] == entry_id and any(a.get("operation") == "addContact" for a in applied):
                    applied.append({"operation": op, "status": "covered_by_addContact", "id": body["id"]})
                    continue
                result = mutations.touch_contact(body["id"], body.get("date"), body.get("source"))
                applied.append({"operation": op, "status": "written", "result": result})
            elif op == "openThread":
                data, _ = mutations._read_threads_file()
                if any(t.get("id") == body["id"] for t in data.get("threads") or []):
                    applied.append({"operation": op, "status": "already_exists", "id": body["id"]})
                else:
                    rc = mutations.cmd_thread_open(type("Ns", (), {
                        "id": body["id"], "title": body["title"], "type": body["type"],
                        "people": body.get("people") or [], "companies": body.get("companies") or [],
                        "context": body.get("context") or "", "state": body.get("current_state") or "",
                        "target_close": body.get("target_close"),
                        "boost_for_brief": body.get("boost_for_brief") or "medium",
                        "boost_score": body.get("boost_score") or 1.2,
                        "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("thread-open failed")
                    applied.append({"operation": op, "status": "written", "id": body["id"]})
            elif op == "loopAdd":
                existing = [
                    L for L in core.parse_loop_ledger()
                    if (not L.closed and L.party == body["party"]
                        and L.description == body["description"])
                ]
                if existing:
                    applied.append({
                        "operation": op,
                        "status": "already_exists",
                        "id": existing[0].id,
                        "party": body["party"],
                    })
                else:
                    rc = mutations.cmd_loop_add(type("Ns", (), {
                        "party": body["party"], "description": body["description"],
                        "target": body["target"], "opened": body.get("opened"),
                        "id": None, "dry_run": False,
                    })())
                    if rc != 0:
                        raise RuntimeError("loop-add failed")
                    applied.append({"operation": op, "status": "written", "party": body["party"]})
            elif op == "updateContact":
                # RB-DEFECT-2026-09-18: canonical phone/email enrichment for
                # an existing contact. Route through mutations.cmd_contact_
                # update -- the same canonical-registry-designated mutation
                # owner as every other baseline field write in this codebase
                # (never a direct baseline_index.json edit). The "before"
                # value is captured BEFORE the write so the receipt reflects
                # the real prior state, and the receipt is only recorded once
                # the write is known to have actually succeeded.
                field_kwargs = {k: v for k, v in body.items() if k != "id"}
                field_name, new_value = next(iter(field_kwargs.items()))
                prior_entry = next((e for e in core.load_baseline() if e.get("id") == body["id"]), None)
                ns_fields = dict(
                    id=body["id"], company=None, role=None, email=None, phone=None,
                    linkedin=None, last_touch=None, signal_class=None, rc_tier=None,
                    rc_state=None, relationship_domain=None, notes=None,
                    notes_replace=False, tags_add=None, dry_run=False,
                )
                ns_fields[field_name] = new_value
                rc = mutations.cmd_contact_update(type("Ns", (), ns_fields)())
                if rc != 0:
                    raise RuntimeError(f"{op} failed")
                applied.append({"operation": op, "status": "written", "id": body["id"], "fields": field_kwargs})
                decision = mutation_policy.decide(
                    source="manual_relationship_intake", new_value=new_value,
                    existing_value=(prior_entry or {}).get(field_name), is_replacement=True,
                    field_name=field_name, entity_id=body["id"],
                )
                mutation_policy.record_receipt(decision, artifact="baseline_index.json", applied=True)
            elif op == "updateEmploymentState":
                # RB-DEFECT-2026-09-18: dated/operator-confirmed employment
                # departure -- clears current_company/current_role while
                # preserving them as last_known_company/last_known_role, via
                # the dedicated command (cmd_contact_update's "None means
                # unchanged" convention can't express "clear this field").
                fields = {k: v for k, v in body.items() if k != "id"}
                rc = mutations.cmd_contact_set_employment_state(type("Ns", (), dict(
                    id=body["id"],
                    employment_status=fields.get("employment_status"),
                    employment_status_source=fields.get("employment_status_source"),
                    employment_status_observed_at=fields.get("employment_status_observed_at"),
                    employment_end_date=fields.get("employment_end_date"),
                    employment_date_confidence=fields.get("employment_date_confidence"),
                    last_known_company=fields.get("last_known_company"),
                    last_known_role=fields.get("last_known_role"),
                    dry_run=False,
                ))())
                if rc != 0:
                    raise RuntimeError(f"{op} failed")
                applied.append({"operation": op, "status": "written", "id": body["id"], "fields": fields})
                decision = mutation_policy.decide(
                    source="manual_relationship_intake",
                    new_value=fields.get("last_known_company"),
                    existing_value=None, is_set_member=True,
                    field_name="employment_status", entity_id=body["id"],
                )
                mutation_policy.record_receipt(decision, artifact="baseline_index.json", applied=True)
            elif op == "writeInteractionBrief":
                path = core.PROJECT_DIR / body["path"]
                result = _write_interaction_brief(
                    path,
                    entry_id=entry_id,
                    name=name,
                    event_at=event_at,
                    company=company,
                    opportunity=opportunity,
                    signals=report.get("signals") or [],
                    opportunity_state=report.get("opportunity_state"),
                )
                applied.append({"operation": op, "status": result["status"], "path": body["path"]})
            else:
                skipped.append({"operation": op, "reason": "unknown_operation", "body": body})
        except Exception as exc:  # noqa: BLE001
            skipped.append({"operation": op, "reason": f"failed:{exc}", "body": body})

    report["persistence"] = {
        "status": "persisted" if applied and not any(s.get("reason", "").startswith("failed:") for s in skipped) else "partial_or_failed",
        "applied": applied,
        "skipped": skipped,
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    report["mutation_visibility"] = {
        "writes_performed": len([a for a in applied if a.get("status") in {"written", "already_exists", "covered_by_addContact"}]),
        "writes_pending_confirmation": 0,
        "blocked_writes": len(skipped),
    }
    report["canonical_response_requirement"]["persistent_state_mutation"] = report["persistence"]["status"]
    return report


def assess(
    *,
    text: str,
    contact_id: str | None = None,
    name: str | None = None,
    event_at: str | None = None,
    captured_at: str | None = None,
    organization: str | None = None,
    opportunity: str | None = None,
    apply: bool = False,
    business_override: bool = False,
) -> dict:
    # RB-2026-08-28: personal vs. business gate, per Todd's explicit
    # direction -- RB is a business/professional relationship-capital tool;
    # personal relationships/content must never be recorded OR reported.
    # Same rule and same system/personal_relationship_exempt.json as
    # relationship_intake.process_relationship_thread() -- checked here too
    # since this is a second, independent intake surface for pasted/
    # screenshot text.
    guard = prg.classify(sender_name=name or "", text=text, explicit_business_flag=business_override)
    if guard.is_personal:
        return {
            "ok": False,
            "personal_content": True,
            "reason": guard.reason,
            "note": (
                f"Classified as personal content ({guard.reason}) -- RB does not "
                "record or report personal relationships. If this is actually "
                "business, re-call with business_override=true."
            ),
        }

    baseline = core.load_baseline()
    entry = _resolve_contact(baseline, contact_id=contact_id, name=name)
    event_date = _parse_date(event_at)
    captured_date = _parse_date(captured_at)
    company = _infer_company(text, organization)
    opportunity_title = _infer_opportunity(text, opportunity)
    signals = _detect_signals(text)
    as_of = captured_date

    # RB-DEFECT-2026-09-18: explicitly supplied contact fields -- phone,
    # email, and a dated former-employer statement -- extracted from the
    # manual RI input so they can be routed through the canonical mutation
    # decision engine instead of being silently dropped (phone/email) or
    # silently ignored (a "formerly at X since <date>" statement, which used
    # to leave current_company pointing at an employer already left).
    contact_fields = _extract_contact_fields(text)
    former_employer_label = _is_former_employer_label(company)
    departure_since = _extract_departure_since(text) if former_employer_label else None

    drr_before = core.drr_score(entry, as_of) if entry else None
    drr_after = None
    if entry:
        projected = deepcopy(entry)
        if not projected.get("last_touch") or event_date.isoformat() > projected.get("last_touch"):
            projected["last_touch"] = event_date.isoformat()
        drr_after = core.drr_score(projected, as_of)
    elif name:
        projected = {
            "id": _slug(name),
            "name": name,
            "current_company": company,
            "current_role": "Recruiting / talent contact",
            "email": None,
            "phone": None,
            "sources": ["manual_relationship_intake"],
            "signal_class": "LKI" if any(s["type"] == "internal_circulation_signal" for s in signals) else "LMI",
            "rc_tier": None,
            "last_touch": event_date.isoformat(),
            "circles": [],
        }
        drr_after = core.drr_score(projected, as_of)

    metrics = _metric_pack(entry, signals, as_of=as_of, event_at=event_date)
    opp_state = _opportunity_state(signals, company=company, opportunity=opportunity_title)
    proposed = _proposed_mutations(
        entry,
        signals,
        event_at=event_date,
        captured_at=captured_date,
        name=(entry.get("name") if entry else name),
        company=company,
        opportunity=opportunity_title,
        phone=contact_fields["phone"],
        email=contact_fields["email"],
        former_employer_label=former_employer_label,
        departure_since=departure_since,
    )

    report = {
        "ok": True,
        "input_type": "manual_relationship_artifact",
        "grounding": "manual_user_provided",
        "event_at": event_date.isoformat(),
        "captured_at": captured_date.isoformat(),
        "rb_match": {
            "status": "matched" if entry else "unmatched_or_unverified",
            "id": entry.get("id") if entry else contact_id,
            "name": entry.get("name") if entry else name,
            "signal_class": entry.get("signal_class") if entry else None,
            "rc_tier": entry.get("rc_tier") if entry else None,
            "current_company": entry.get("current_company") if entry else None,
            "last_touch": entry.get("last_touch") if entry else None,
        },
        "signals": signals,
        "metrics": metrics,
        "opportunity_state": opp_state,
        "drr_projection": {
            "before": drr_before,
            "after": drr_after,
            "delta": (
                round(drr_after["score"] - drr_before["score"], 1)
                if drr_before and drr_after else None
            ),
        },
        "proposed_mutations": proposed,
        "mutation_visibility": {
            "writes_performed": 0,
            "writes_pending_confirmation": sum(1 for m in proposed if m.get("safe_to_write")),
            "blocked_writes": sum(1 for m in proposed if not m.get("safe_to_write")),
        },
        "persistence": {
            "status": "not_persisted" if not apply else "pending_apply",
            "applied": [],
            "skipped": [],
        },
        "canonical_response_requirement": {
            "structured_signal_detection": bool(signals),
            "quantified_trust_intelligence": True,
            "persistent_state_mutation": "pending_confirmation" if not apply else "pending_apply",
            "observable_operational_outputs": bool(proposed),
        },
    }
    if apply:
        report = apply_mutations(report)
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", dest="contact_id")
    p.add_argument("--name")
    p.add_argument("--event-at")
    p.add_argument("--captured-at")
    p.add_argument("--organization")
    p.add_argument("--opportunity")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.text_file:
        text = Path(args.text_file).read_text()
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()
    report = assess(
        text=text,
        contact_id=args.contact_id,
        name=args.name,
        event_at=args.event_at,
        captured_at=args.captured_at,
        organization=args.organization,
        opportunity=args.opportunity,
        apply=args.apply,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
