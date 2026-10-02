#!/usr/bin/env python3
"""
action_drafts.py — draft-ready action rendering for RB 9.19.

Converts DraftSpec objects (derived from RB recommended actions) into
preview-only draft text in Todd Vahlsing / BridgePoint Ops voice and
within BridgePoint entity boundaries.

INVARIANTS (enforced, non-negotiable):
  - send_allowed is always False.
  - requires_user_review is always True.
  - Drafts are evidence-bounded: no invented facts, history, or confidence.
  - Passive/social/vendor signals are NOT phrased as verified fact.
  - BridgePoint engagement boundaries are respected.
  - No auto-send path exists in this module.

Usage:
    python3 action_drafts.py --smoke
    python3 action_drafts.py --json
    python3 action_drafts.py --limit 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

# ---------------------------------------------------------------------------
# Profile constants — Todd / BridgePoint Ops
# ---------------------------------------------------------------------------

SENDER_FIRST_NAME = "Todd"
SENDER_FULL_NAME = "Todd Vahlsing"
SENDER_COMPANY = "BridgePoint Ops"

# LinkedIn DM and SMS caps
LINKEDIN_MAX_CHARS = 300
SMS_MAX_CHARS = 160

# Phrases that must never appear in any rendered draft (voice guard).
_BANNED_PHRASES = [
    "I hope this email finds you well",
    "touching base",
    "just checking in",
    "circle back",
    "synergy",
    "leveraging",
    "game-changing",
    "disrupting",
    "I wanted to reach out",
    "per my last email",
    "as per",
    "let's connect",
    "excited to share",
    "I'd love to pick your brain",
]

# Passive signal source keywords. If a contact's last_touch source contains
# any of these, treat the recency signal as passive/uncertain.
_PASSIVE_SOURCE_TOKENS = ("linkedin", "social", "feed", "post", "passive", "vendor")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DraftSpec:
    """Structured specification for a recommended relationship action.

    Built from RB signals (crossings, loops, overlays). Does not contain
    rendered text — that is produced by render_draft().
    """
    spec_id: str
    action_type: str        # follow_up | reconnect | intro_request | thank_you | linkedin_message | text_message
    channel: str            # email | linkedin | sms
    contact_id: str
    contact_name: str
    contact_company: Optional[str]
    contact_role: Optional[str]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    contact_linkedin: Optional[str]
    rc_tier: Optional[str]
    days_since_touch: Optional[int]
    last_touch: Optional[str]
    urgency: str            # overdue | due_today | this_week | routine
    evidence: list = field(default_factory=list)   # plain-English evidence statements
    evidence_strength: str = "moderate"            # strong | moderate | weak
    thread_ids: list = field(default_factory=list) # active thread IDs
    company_context: Optional[str] = None
    intro_target_name: Optional[str] = None        # for intro_request only
    intro_target_company: Optional[str] = None     # for intro_request only
    intro_reason: Optional[str] = None             # broker proximity reason
    last_touch_source: Optional[str] = None        # source of last_touch signal
    is_engagement_restricted: bool = False
    restriction_reason: Optional[str] = None


@dataclass
class RenderedDraft:
    """A preview-only rendered draft. NEVER auto-sent.

    send_allowed and requires_user_review are locked fields — they cannot be
    overridden to True/False respectively. Any serializer must preserve them.
    """
    draft_id: str
    spec_id: str
    channel: str
    action_type: str
    subject: Optional[str]          # email only
    body: str
    tone_suggestion: str
    evidence_used: list
    omitted_due_to_uncertainty: list
    safety_flags: list
    send_allowed: bool = False
    requires_user_review: bool = True

    def __post_init__(self) -> None:
        # Hard invariants — these fields are not configurable.
        object.__setattr__(self, "send_allowed", False)
        object.__setattr__(self, "requires_user_review", True)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["send_allowed"] = False
        d["requires_user_review"] = True
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_id(action_type: str, contact_id: str, today: date) -> str:
    raw = f"{action_type}::{contact_id}::{today.isoformat()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _draft_id(spec_id: str) -> str:
    raw = f"draft::{spec_id}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _evidence_strength(
    days_since_touch: Optional[int],
    sources_count: int,
    last_touch_source: Optional[str] = None,
) -> str:
    src = (last_touch_source or "").lower()
    if any(tok in src for tok in _PASSIVE_SOURCE_TOKENS):
        return "weak"
    if days_since_touch is None:
        return "weak"
    if sources_count >= 3 and days_since_touch < 180:
        return "strong"
    if sources_count >= 1 and days_since_touch < 365:
        return "moderate"
    return "weak"


def _is_restricted(entry: dict) -> tuple[bool, Optional[str]]:
    """Check BridgePoint engagement boundaries from notes/tags."""
    notes = (entry.get("notes") or "").lower()
    tags = [(t or "").lower() for t in (entry.get("tags") or [])]
    if ("free" in notes and "advice" in notes) or "free_advice" in tags:
        return True, "free-advice positioning detected"
    if "commission-only" in notes or "commission_only" in tags:
        return True, "commission-only engagement flagged"
    if "intro-only" in notes or "intro_only" in tags:
        return True, "intro-only positioning flagged"
    return False, None


def _preferred_channel(entry: dict, action_type: str) -> str:
    if action_type == "linkedin_message":
        return "linkedin"
    if action_type == "text_message":
        return "sms"
    if entry.get("email"):
        return "email"
    if entry.get("linkedin_url"):
        return "linkedin"
    return "email"


def _first_name(full_name: str) -> str:
    return (full_name or "").strip().split()[0] if full_name else ""


def _days_label(days: Optional[int]) -> str:
    if days is None:
        return "a while"
    if days < 45:
        return f"about {days} days"
    if days < 90:
        return "a few weeks"
    if days < 180:
        return "several months"
    if days < 365:
        return "quite a few months"
    return f"over {days // 365} year{'s' if days >= 730 else ''}"


def _passive_signal_for(entry: dict) -> bool:
    """True if the contact's last-touch source is passive (social/vendor)."""
    src = (entry.get("last_touch_source") or "").lower()
    return any(tok in src for tok in _PASSIVE_SOURCE_TOKENS)


def _guard_body(body: str) -> str:
    """Strip any banned phrases that slipped through template logic."""
    for phrase in _BANNED_PHRASES:
        if phrase.lower() in body.lower():
            body = body.replace(phrase, "[FLAGGED_PHRASE_REMOVED]")
    return body


# ---------------------------------------------------------------------------
# Spec builder
# ---------------------------------------------------------------------------


def build_draft_specs(
    baseline: list[dict],
    crossings: list,
    loops_buckets: dict,
    threads: list[dict],
    today: date,
    *,
    max_specs: int = 25,
) -> list[DraftSpec]:
    """Produce DraftSpec objects from current RB signals.

    Priority order:
      1. Dormancy crossings → follow_up (email/linkedin) or reconnect (email)
      2. Inner-tier RCs with phone and overdue → text_message
      3. LKI contacts with active thread boost → linkedin_message
      4. Active high-boost threads with company targets → intro_request
    """
    specs: list[DraftSpec] = []
    seen_contacts: set[str] = set()

    baseline_idx: dict[str, dict] = {e["id"]: e for e in baseline}

    # Thread boost map: contact_id -> (boost, thread_ids)
    thread_boost_map: dict[str, tuple[float, list]] = {}
    for e in baseline:
        boost, tids = core.thread_boost_for(e, threads)
        if tids:
            thread_boost_map[e["id"]] = (boost, tids)

    # 1. Dormancy crossings → follow_up or reconnect
    for c in crossings:
        if hasattr(c, "id"):
            cid = c.id; cname = c.name; ctier = c.tier
            days = c.days_ago; lt = c.last_touch
            overage = c.overage or 0
            company = c.company
        else:
            cid = c["id"]; cname = c["name"]; ctier = c["tier"]
            days = c.get("days_ago"); lt = c.get("last_touch")
            overage = c.get("overage") or 0
            company = c.get("company")

        if cid in seen_contacts:
            continue

        entry = baseline_idx.get(cid, {})
        restricted, restriction_reason = _is_restricted(entry)
        sources_count = len(entry.get("sources") or [])
        lt_source = entry.get("last_touch_source")
        ev_strength = _evidence_strength(days, sources_count, lt_source)
        tids = thread_boost_map.get(cid, (1.0, []))[1]

        # Inner tier with phone → text_message (in addition to email)
        if ctier == "inner" and entry.get("phone") and not seen_contacts:
            lt_str = lt.isoformat() if hasattr(lt, "isoformat") else lt
            text_spec = DraftSpec(
                spec_id=_spec_id("text_message", cid, today),
                action_type="text_message",
                channel="sms",
                contact_id=cid,
                contact_name=cname,
                contact_company=company,
                contact_role=entry.get("role"),
                contact_email=None,
                contact_phone=entry.get("phone"),
                contact_linkedin=entry.get("linkedin_url"),
                rc_tier=ctier,
                days_since_touch=days,
                last_touch=lt_str,
                urgency="overdue",
                evidence=[
                    f"{days} days since last touch (inner-tier threshold: {core.TIER_THRESHOLD_DAYS['inner']} days)"
                ],
                evidence_strength=ev_strength,
                thread_ids=tids,
                company_context=company,
                last_touch_source=lt_source,
                is_engagement_restricted=restricted,
                restriction_reason=restriction_reason,
            )
            specs.append(text_spec)

        # Main channel action
        action_type = (
            "reconnect" if ctier == "dormant_valuable" and overage > 180
            else "follow_up"
        )
        channel = _preferred_channel(entry, action_type)
        urgency = "overdue"
        lt_str = lt.isoformat() if hasattr(lt, "isoformat") else lt

        evidence = [
            f"{days} days since last touch "
            f"(threshold: {core.TIER_THRESHOLD_DAYS.get(ctier, 90)} days, overage: {overage}d)"
        ]
        if tids:
            evidence.append(f"active thread(s): {', '.join(tids)}")
        if _passive_signal_for(entry):
            evidence.append("last-touch source is passive (social signal — not confirmed direct contact)")

        spec = DraftSpec(
            spec_id=_spec_id(action_type, cid, today),
            action_type=action_type,
            channel=channel,
            contact_id=cid,
            contact_name=cname,
            contact_company=company,
            contact_role=entry.get("role"),
            contact_email=entry.get("email"),
            contact_phone=entry.get("phone"),
            contact_linkedin=entry.get("linkedin_url"),
            rc_tier=ctier,
            days_since_touch=days,
            last_touch=lt_str,
            urgency=urgency,
            evidence=evidence,
            evidence_strength=ev_strength,
            thread_ids=tids,
            company_context=company,
            last_touch_source=lt_source,
            is_engagement_restricted=restricted,
            restriction_reason=restriction_reason,
        )
        specs.append(spec)
        seen_contacts.add(cid)

        if len(specs) >= max_specs:
            break

    # 2. LKI contacts with active thread boost → linkedin_message
    if len(specs) < max_specs:
        lki_boosted = [
            e for e in baseline
            if e.get("signal_class") == "LKI"
            and e.get("id") in thread_boost_map
            and e.get("linkedin_url")
            and e["id"] not in seen_contacts
        ]
        for e in lki_boosted[:5]:
            eid = e["id"]
            _, tids = thread_boost_map[eid]
            restricted, restriction_reason = _is_restricted(e)
            lt_source = e.get("last_touch_source")
            spec = DraftSpec(
                spec_id=_spec_id("linkedin_message", eid, today),
                action_type="linkedin_message",
                channel="linkedin",
                contact_id=eid,
                contact_name=e.get("name", ""),
                contact_company=e.get("current_company"),
                contact_role=e.get("role"),
                contact_email=e.get("email"),
                contact_phone=None,
                contact_linkedin=e.get("linkedin_url"),
                rc_tier=e.get("rc_tier"),
                days_since_touch=None,
                last_touch=e.get("last_touch"),
                urgency="routine",
                evidence=[f"active thread boost — threads: {', '.join(tids)}"],
                evidence_strength="moderate",
                thread_ids=tids,
                company_context=e.get("current_company"),
                last_touch_source=lt_source,
                is_engagement_restricted=restricted,
                restriction_reason=restriction_reason,
            )
            specs.append(spec)
            seen_contacts.add(eid)

    # 3. Intro requests from high-boost threads with company targets
    if len(specs) < max_specs:
        high_threads = [
            t for t in threads
            if t.get("boost_for_brief") == "high" and t.get("companies")
        ]
        for thread in high_threads[:3]:
            if len(specs) >= max_specs:
                break
            for target_company in (thread.get("companies") or [])[:1]:
                # Check if there's already an RC insider at this company
                insiders = [
                    e for e in baseline
                    if e.get("signal_class") == "RC"
                    and (e.get("current_company") or "").lower() == target_company.lower()
                ]
                if insiders:
                    continue  # Already have an insider — no intro needed
                # Find broker path
                paths = core.find_intro_paths(
                    target_company, limit=1, today=today
                )
                brokers = paths.get("candidate_brokers") or []
                if not brokers:
                    continue
                broker = brokers[0]
                broker_id = broker.get("id") or ""
                broker_name = broker.get("name") or ""
                if broker_id in seen_contacts:
                    continue
                broker_entry = baseline_idx.get(broker_id, {})
                restricted, restriction_reason = _is_restricted(broker_entry)
                spec = DraftSpec(
                    spec_id=_spec_id("intro_request", broker_id or target_company, today),
                    action_type="intro_request",
                    channel="email" if broker_entry.get("email") else "linkedin",
                    contact_id=broker_id,
                    contact_name=broker_name,
                    contact_company=broker_entry.get("current_company"),
                    contact_role=broker_entry.get("role"),
                    contact_email=broker_entry.get("email"),
                    contact_phone=None,
                    contact_linkedin=broker_entry.get("linkedin_url"),
                    rc_tier=broker_entry.get("rc_tier"),
                    days_since_touch=None,
                    last_touch=broker_entry.get("last_touch"),
                    urgency="routine",
                    evidence=[
                        f"thread '{thread.get('id')}' is targeting {target_company}",
                        f"broker composite score: {broker.get('composite_score', '—')}",
                    ],
                    evidence_strength="moderate",
                    thread_ids=[thread.get("id", "")],
                    company_context=target_company,
                    intro_target_name=None,
                    intro_target_company=target_company,
                    intro_reason=broker.get("reason", ""),
                    is_engagement_restricted=restricted,
                    restriction_reason=restriction_reason,
                )
                specs.append(spec)

    return specs[:max_specs]


def count_draft_actions(
    baseline: list[dict],
    crossings: list,
    threads: list[dict],
    today: date,
) -> int:
    """Lightweight count of draft actions for the daily brief headline."""
    specs = build_draft_specs(baseline, crossings, {}, threads, today, max_specs=50)
    return len(specs)


# ---------------------------------------------------------------------------
# Draft renderer
# ---------------------------------------------------------------------------


def render_draft(spec: DraftSpec) -> RenderedDraft:
    """Render a DraftSpec into preview-only text. Evidence-bounded, voice-guarded.

    The rendered body references ONLY facts present in the DraftSpec fields.
    Any field that is None is omitted from the body text rather than invented.
    """
    first = _first_name(spec.contact_name)
    days_label = _days_label(spec.days_since_touch)
    omitted: list[str] = []
    safety_flags: list[str] = []
    evidence_used: list[str] = list(spec.evidence)

    # Passive/weak signal warning
    is_passive = any(tok in (spec.last_touch_source or "").lower() for tok in _PASSIVE_SOURCE_TOKENS)
    if is_passive:
        safety_flags.append(
            "Last-touch source is a passive signal (social/vendor). "
            "Do not cite specific prior contact details as if they were verified."
        )
        omitted.append("Direct contact recency (last-touch source is passive, not confirmed)")

    if spec.evidence_strength == "weak":
        safety_flags.append(
            "Evidence basis is limited or stale. Verify this contact's current situation before sending."
        )

    if spec.is_engagement_restricted:
        safety_flags.append(
            f"BridgePoint engagement restriction: {spec.restriction_reason}. "
            "Do not offer free advice, unpaid advisory, or intro-only terms."
        )

    subject: Optional[str] = None
    body: str

    # -----------------------------------------------------------------------
    # Per-action-type rendering
    # -----------------------------------------------------------------------

    if spec.action_type == "follow_up":
        subject, body = _render_follow_up(spec, first, days_label, omitted, safety_flags)

    elif spec.action_type == "reconnect":
        subject, body = _render_reconnect(spec, first, days_label, omitted, safety_flags)

    elif spec.action_type == "intro_request":
        subject, body = _render_intro_request(spec, first, omitted, safety_flags)

    elif spec.action_type == "thank_you":
        subject, body = _render_thank_you(spec, first, omitted, safety_flags)

    elif spec.action_type == "linkedin_message":
        subject, body = _render_linkedin_message(spec, first, omitted, safety_flags)

    elif spec.action_type == "text_message":
        subject, body = _render_text_message(spec, first, omitted, safety_flags)

    else:
        subject = None
        body = f"[Unsupported action type: {spec.action_type}]"
        safety_flags.append(f"Unrecognized action type '{spec.action_type}' — do not send")

    body = _guard_body(body)

    tone = "Direct and warm. Midwestern cadence. No buzzwords, no AI fluff. Natural and human."

    draft = RenderedDraft(
        draft_id=_draft_id(spec.spec_id),
        spec_id=spec.spec_id,
        channel=spec.channel,
        action_type=spec.action_type,
        subject=subject,
        body=body.strip(),
        tone_suggestion=tone,
        evidence_used=evidence_used,
        omitted_due_to_uncertainty=omitted,
        safety_flags=safety_flags,
        send_allowed=False,
        requires_user_review=True,
    )
    return draft


def _render_follow_up(
    spec: DraftSpec, first: str, days_label: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    subject = f"Checking in, {first}" if first else "Checking in"

    # Company line — only if company is known
    company_line = ""
    if spec.contact_company:
        company_line = f"Hope things at {spec.contact_company} are going well."
    else:
        omitted.append("Current company not referenced (not verified in baseline)")

    # Thread line — only if there's a relevant active thread
    thread_line = ""
    if spec.thread_ids:
        thread_line = (
            "I've got some things moving on my end that your perspective might be useful on. "
            "I'll save the details for when we talk."
        )

    body_parts = [f"{first}," if first else "Hello,", ""]

    if spec.evidence_strength == "weak" or any(
        tok in (spec.last_touch_source or "").lower() for tok in _PASSIVE_SOURCE_TOKENS
    ):
        # Hedged language for weak/passive evidence
        body_parts.append(
            f"It's been {days_label} — figured it was past time to reconnect."
        )
        omitted.append("Specific prior interaction not referenced (evidence basis uncertain)")
    else:
        body_parts.append(
            f"It's been {days_label} since we've connected, and I wanted to fix that."
        )

    if company_line:
        body_parts.append(company_line)
    if thread_line:
        body_parts.append("")
        body_parts.append(thread_line)

    body_parts += [
        "",
        "If you have 30 minutes in the coming weeks, I'd be glad to catch up.",
        "",
        f"{SENDER_FIRST_NAME}",
        SENDER_COMPANY,
    ]

    return subject, "\n".join(body_parts)


def _render_reconnect(
    spec: DraftSpec, first: str, days_label: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    subject = "Long overdue"

    company_line = ""
    if spec.contact_company:
        company_line = f"I know things at {spec.contact_company} keep moving."
    else:
        omitted.append("Current company not referenced (not verified in baseline)")

    # For reconnects, acknowledge the gap explicitly
    body_parts = [f"{first}," if first else "Hello,", ""]

    body_parts.append(
        f"It's been {days_label} since we last connected — that's too long, and that's on me."
    )

    if company_line:
        body_parts.append(company_line)

    if spec.evidence_strength == "weak":
        omitted.append("Context of prior engagement not referenced (evidence too stale to cite accurately)")
        body_parts.append(
            "A lot has happened on my end. I'd value the chance to catch up when you have time."
        )
    else:
        body_parts.append(
            "A lot has happened on my end. I've been building out BridgePoint Ops — "
            "advisory work in restaurant tech. I'd value the chance to catch up."
        )

    body_parts += [
        "",
        "Any time in the next few weeks that works for a conversation?",
        "",
        f"{SENDER_FIRST_NAME}",
        SENDER_COMPANY,
    ]

    return subject, "\n".join(body_parts)


def _render_intro_request(
    spec: DraftSpec, first: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    subject = "Quick ask"

    target = spec.intro_target_company or spec.intro_target_name or "a contact"

    # Proximity reason — only include if the engine gave a reason and it's concrete
    reason_line = ""
    if spec.intro_reason and len(spec.intro_reason.strip()) > 10:
        reason_line = spec.intro_reason.strip()
    else:
        omitted.append("Broker's specific relationship with target not cited (reason not verified)")

    # Safety: always include opt-out / no-pressure language
    body_parts = [f"{first}," if first else "Hello,", ""]

    body_parts.append(
        f"I'm trying to find the right way into {target}. "
        f"Given your background, you might be the right connection."
    )

    if reason_line:
        body_parts += ["", reason_line]

    body_parts += [
        "",
        "If you think it's a fit and you're comfortable making the connection, I'd appreciate "
        "the introduction. No pressure at all if the timing isn't right or you'd rather pass — "
        "I wanted to ask directly rather than assume.",
        "",
        f"{SENDER_FIRST_NAME}",
        SENDER_COMPANY,
    ]

    safety_flags.append(
        "Intro request: confirm broker actually knows the target before sending. "
        "Opt-out language is present — do not remove it."
    )

    return subject, "\n".join(body_parts)


def _render_thank_you(
    spec: DraftSpec, first: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    subject = "Good talking today"

    # Only reference the meeting if we have evidence (company context or thread)
    meeting_line = ""
    if spec.company_context:
        meeting_line = f"Appreciate the time today — the conversation was worth it."
    else:
        meeting_line = "Appreciate the time today."
        omitted.append("Meeting-specific context not referenced (no verified meeting detail in spec)")

    body_parts = [f"{first}," if first else "Hello,", ""]
    body_parts.append(meeting_line)

    if spec.thread_ids:
        body_parts += [
            "",
            "I'll follow up on the specifics we discussed as things develop on my end.",
        ]
        omitted.append("Meeting discussion details not referenced (not in evidence)")

    body_parts += [
        "",
        f"{SENDER_FIRST_NAME}",
        SENDER_COMPANY,
    ]

    return subject, "\n".join(body_parts)


def _render_linkedin_message(
    spec: DraftSpec, first: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    # LinkedIn DMs: short, warm, professional. Hard cap at LINKEDIN_MAX_CHARS.
    company_ctx = f" at {spec.contact_company}" if spec.contact_company else ""

    is_passive = any(tok in (spec.last_touch_source or "").lower() for tok in _PASSIVE_SOURCE_TOKENS)

    if is_passive:
        # Passive source: cannot reference seeing their content as fact
        base = (
            f"{first} — thought it was time to reconnect{company_ctx}. "
            f"Would value a conversation when you have a few minutes. — {SENDER_FIRST_NAME}"
        )
        omitted.append("Social post content not referenced as basis for outreach (passive signal)")
    else:
        base = (
            f"{first} — been a while. Would value connecting when you have a few minutes"
            f"{', and comparing notes on ' + spec.contact_company if spec.contact_company else ''}. "
            f"— {SENDER_FIRST_NAME}"
        )

    # Enforce hard cap
    if len(base) > LINKEDIN_MAX_CHARS:
        base = base[: LINKEDIN_MAX_CHARS - 3] + "..."
        safety_flags.append(f"LinkedIn DM truncated to {LINKEDIN_MAX_CHARS} chars")

    safety_flags.append("LinkedIn DMs are for connection and conversation — no business pitch in this message.")

    return None, base


def _render_text_message(
    spec: DraftSpec, first: str,
    omitted: list, safety_flags: list,
) -> tuple[Optional[str], str]:
    # SMS: very short, personal, first-name basis
    base = f"Hey {first} — been a while. Would love to catch up when you get a chance. — {SENDER_FIRST_NAME}"

    if len(base) > SMS_MAX_CHARS:
        base = f"Hey {first} — been a while. Let's connect. — {SENDER_FIRST_NAME}"

    if len(base) > SMS_MAX_CHARS:
        base = base[: SMS_MAX_CHARS - 3] + "..."
        safety_flags.append(f"SMS truncated to {SMS_MAX_CHARS} chars")

    safety_flags.append("Confirm contact has given consent to receive SMS from Todd before sending.")
    omitted.append("Meeting context and company specifics not included (SMS format)")

    return None, base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_drafts(specs: list[DraftSpec]) -> list[RenderedDraft]:
    return [render_draft(s) for s in specs]


def build_rendered_drafts(
    baseline: list[dict],
    crossings: list,
    loops_buckets: dict,
    threads: list[dict],
    today: date,
    *,
    max_specs: int = 25,
) -> list[RenderedDraft]:
    specs = build_draft_specs(baseline, crossings, loops_buckets, threads, today, max_specs=max_specs)
    return render_drafts(specs)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def smoke_test() -> int:
    """Quick sanity check: load real data, build specs, render drafts, assert invariants."""
    today = date.today()
    baseline = core.load_baseline()
    threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    crossings, _ = core.compute_crossings(baseline, today)
    loops = core.parse_loop_ledger()
    loops_buckets = core.loops_by_status(loops, today)

    specs = build_draft_specs(baseline, crossings, loops_buckets, threads, today, max_specs=10)
    drafts = render_drafts(specs)

    errors: list[str] = []
    for d in drafts:
        if d.send_allowed is not False:
            errors.append(f"INVARIANT FAIL: draft {d.draft_id} has send_allowed={d.send_allowed}")
        if d.requires_user_review is not True:
            errors.append(f"INVARIANT FAIL: draft {d.draft_id} has requires_user_review={d.requires_user_review}")
        if not d.body:
            errors.append(f"INVARIANT FAIL: draft {d.draft_id} has empty body")
        if d.channel == "linkedin" and len(d.body) > LINKEDIN_MAX_CHARS + 10:
            errors.append(f"INVARIANT FAIL: LinkedIn draft {d.draft_id} exceeds char cap")
        if d.channel == "sms" and len(d.body) > SMS_MAX_CHARS + 10:
            errors.append(f"INVARIANT FAIL: SMS draft {d.draft_id} exceeds char cap")
        if not d.spec_id:
            errors.append(f"INVARIANT FAIL: draft {d.draft_id} has no spec_id")
        dd = d.to_dict()
        if dd.get("send_allowed") is not False:
            errors.append(f"INVARIANT FAIL: to_dict() mutated send_allowed on {d.draft_id}")

        # Check for banned phrases in body
        for phrase in _BANNED_PHRASES:
            if phrase.lower() in d.body.lower():
                errors.append(f"VOICE FAIL: banned phrase '{phrase}' in draft {d.draft_id}")

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Smoke OK — {len(specs)} spec(s) built, {len(drafts)} draft(s) rendered.")
    for d in drafts[:3]:
        print(f"  [{d.action_type}/{d.channel}] {d.contact_name if hasattr(d, 'contact_name') else ''} — {(d.subject or d.body[:60])!r}")
    # Print spec contact names via spec list correlation
    for spec, draft in zip(specs[:3], drafts[:3]):
        print(f"  [{draft.action_type}/{draft.channel}] {spec.contact_name!r} spec_id={spec.spec_id}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(description="RB 9.19 draft-ready actions.")
    p.add_argument("--smoke", action="store_true", help="Run invariant smoke test and exit.")
    p.add_argument("--json", action="store_true", help="Emit JSON list of rendered drafts.")
    p.add_argument("--specs", action="store_true", help="Emit JSON list of DraftSpec objects only.")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--date", help="ISO date (default: system date)")
    p.add_argument("--cache", action="store_true",
                   help="Write draft specs and rendered drafts to system/.cache/action_drafts.json")
    args = p.parse_args()

    if args.smoke:
        return smoke_test()

    today = date.fromisoformat(args.date) if args.date else date.today()
    baseline = core.load_baseline()
    threads = [t for t in core.load_active_threads() if t.get("status") == "open"]
    crossings, _ = core.compute_crossings(baseline, today)
    loops = core.parse_loop_ledger()
    loops_buckets = core.loops_by_status(loops, today)

    specs = build_draft_specs(baseline, crossings, loops_buckets, threads, today, max_specs=args.limit)

    if args.specs:
        print(json.dumps([asdict(s) for s in specs], indent=2, default=str))
        return 0

    drafts = render_drafts(specs)

    if args.cache:
        core.write_cache("action_drafts", {
            "date": today.isoformat(),
            "draft_count": len(drafts),
            "specs": [asdict(s) for s in specs],
            "drafts": [d.to_dict() for d in drafts],
        }, source="action_drafts.py")

    if args.json:
        print(json.dumps([d.to_dict() for d in drafts], indent=2, default=str))
        return 0

    print(f"Draft-ready actions as of {today} — {len(drafts)} draft(s). PREVIEW ONLY. send_allowed=false.\n")
    for spec, draft in zip(specs, drafts):
        print(f"{'='*70}")
        print(f"[{draft.action_type.upper()} / {draft.channel.upper()}] → {spec.contact_name}")
        if spec.contact_company:
            print(f"Company: {spec.contact_company}")
        if draft.subject:
            print(f"Subject: {draft.subject}")
        print(f"\n{draft.body}\n")
        if draft.safety_flags:
            for flag in draft.safety_flags:
                print(f"  ⚠ {flag}")
        if draft.omitted_due_to_uncertainty:
            print(f"  Omitted: {'; '.join(draft.omitted_due_to_uncertainty)}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
