#!/usr/bin/env python3
"""
relationship_signals.py — last-24-hour relationship signal detection.

Implements Phase 9 of OPERATIONALIZATION.md and the highest-priority next
build sequence in CLAUDE_HANDOFF.md. Promotes the most relationally relevant
events of the last N hours into a first-class CoS-style daily-brief section.

This is not inbox management. It is signal interpretation. The script:

  1. Reads the canonical overlays (email, calendar, messages, calls, social,
     social outbound) plus baseline + active threads.
  2. Filters events to the look-back window (default last 24 hours).
  3. Classifies each event with a `signal_type`, `signal_strength`,
     `strategic_relevance`, `recommended_action`, `reasoning`, and
     `evidence`.
  4. Produces reconciliation prompts where RB has enough signal to know
     something matters but not enough certainty to mutate state.
  5. Surfaces stale-source warnings so the brief tells the operator when
     a feed has not refreshed inside the freshness threshold.
  6. Sorts everything by (strategic_relevance, signal_strength, recency).

The script writes nothing to canonical state. It is read-only compute. Daily
brief integration and `GET /relationship_signals` consume the report dict.

Usage:
    python3 relationship_signals.py                  # today, 24h window, text
    python3 relationship_signals.py --json           # machine-readable
    python3 relationship_signals.py --hours 48       # widen the window
    python3 relationship_signals.py --date 2026-05-18
    python3 relationship_signals.py --cache          # also write to .cache/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# Per-source freshness thresholds. If fetched_at is older than this we emit a
# stale-source warning. These mirror the operator's expectation: email and
# calendar should refresh daily; direct interaction streams should be near-
# real-time once the Mac fetcher is wired; social refreshes manually.
FRESHNESS_HOURS = {
    "email": 24,
    "calendar": 24,
    "messages": 12,
    "calls": 12,
    "social": 48,
    "social_outbound": 48,
}

# Strategic-relevance ordering used when sorting signals.
RELEVANCE_RANK = {"high": 0, "medium": 1, "low": 2}

# Cheap pattern packs to classify email subjects. These are intentionally
# conservative; we'd rather miss a label than mis-label a signal.
JOB_RX = re.compile(r"\b(interview|offer|recruit(er|ing)?|hiring manager|role|position|requisition|talent acquisition)\b", re.I)
INTRO_RX = re.compile(r"\b(intro(duce|ducing|duction)?|connect(ing)?|meet|warm intro)\b", re.I)
COLLAB_RX = re.compile(r"\b(consult(ing)?|partnership|collaborat\w*|advisor|engagement|proposal|sow)\b", re.I)
SCHEDULE_RX = re.compile(r"\b(calendly|book a time|schedule|when works|deep dive|walkthrough|next steps)\b", re.I)
OPPORTUNITY_RX = re.compile(r"\b(opportunit\w*|opening|pipeline|deal|customer|win)\b", re.I)
COMMUNICATION_FAILURE_RX = re.compile(
    r"\b(email(?:s)?\s+(?:bounced|bounce(?:d)?\s+back|failed|not\s+deliver(?:ed|able))"
    r"|bounce(?:d)?\s+back|delivery\s+(?:status|failure|failed)|undeliver(?:ed|able)"
    r"|couldn'?t\s+(?:reach|email|get\s+through)|tried\s+(?:to\s+)?(?:reach|email|send)"
    r"|dns|nameserver|mx\s+record|email\s+routing|domain\s+(?:issue|outage))\b",
    re.I,
)
CHANNEL_ESCALATION_RX = re.compile(
    r"\b(text(?:ed|ing)?\s+(?:you|me)|via\s+(?:text|sms|imessage)|tracked\s+(?:you|me)\s+down"
    r"|switch(?:ed|ing)\s+(?:to|channels?)|reach(?:ed)?\s+out\s+(?:by|via|on)\s+(?:text|sms|imessage|phone))\b",
    re.I,
)

# Subject patterns we explicitly suppress as low-value noise even when the
# sender is in baseline.
NOISE_SUBJECT_RX = re.compile(
    r"\b(newsletter|digest|unsubscribe|receipt|invoice paid|out of office|automatic reply|delivery status)\b",
    re.I,
)


# ----------------------------------------------------------------------------
# Time helpers
# ----------------------------------------------------------------------------

def _parse_dt(value: str | None) -> datetime | None:
    """Best-effort parse of any timestamp shape we see in the inbox.
    Returns a UTC-aware datetime or None."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Date-only — anchor at midnight UTC
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # ISO with Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_ago(dt: datetime | None, now: datetime) -> float | None:
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def _in_window(dt: datetime | None, window_start: datetime, window_end: datetime) -> bool:
    if dt is None:
        return False
    return window_start <= dt <= window_end


# ----------------------------------------------------------------------------
# Signal data model
# ----------------------------------------------------------------------------

def _new_signal(*, contact_id, name, source, event_at, signal_type,
                signal_strength, strategic_relevance, recommended_action,
                reasoning, evidence, confidence,
                active_thread_ids=None, reconciliation_prompt=None,
                grounding="system_detected", freshness="fresh") -> dict:
    """Construct a signal dict.

    `grounding` is the trust label for the signal payload:
      - `system_detected`      — observed directly in a connected source
                                 overlay within the look-back window.
      - `inferred`             — synthesized from rules (e.g. dormancy
                                 crossings, suppression decisions).
      - `manual_user_provided` — sourced from operator-typed state such as
                                 active_threads.yaml or RC card narrative.
      - `stale_source_limited` — set by build_report when the underlying
                                 source is past its freshness threshold;
                                 implies the "no signal" or "this signal"
                                 conclusion is incomplete.

    `freshness` is independent of `grounding` and tracks whether the
    underlying feed is current: `fresh` or `stale_source_limited`. Setting
    `freshness=stale_source_limited` does NOT change `grounding`; it labels
    the surrounding feed state so callers know the signal may be a partial
    view.
    """
    return {
        "contact_id": contact_id,
        "name": name,
        "source": source,
        "event_at": event_at,
        "signal_type": signal_type,
        "signal_strength": round(float(signal_strength), 3),
        "strategic_relevance": strategic_relevance,
        "recommended_action": recommended_action,
        "reasoning": reasoning,
        "evidence": evidence or [],
        "confidence": round(float(confidence), 3),
        "active_thread_ids": list(active_thread_ids or []),
        "reconciliation_prompt": reconciliation_prompt,
        "grounding": grounding,
        "freshness": freshness,
    }


def _is_nontracked_personal_contact(entry: dict | None) -> bool:
    """True when a baseline contact is intentionally outside RB professional RI.

    Personal/family contacts may remain in baseline for identity resolution, but
    they should not create relationship signals, last-touch prompts, or action
    debt in the CoS operating board.
    """
    if not entry:
        return False
    domain = str(entry.get("relationship_domain") or "").strip().lower()
    tags = {str(t).strip().lower() for t in (entry.get("tags") or [])}
    notes = str(entry.get("notes") or "").lower()
    return (
        domain in {"personal", "family"}
        or "do_not_track" in tags
        or "personal_friend" in tags
        or "best friend" in notes
    )


# Map each signal `source` string to the set of stale-source names that
# render the signal's source non-current. messages_calls collapses two
# underlying feeds, so it is stale if either is stale.
_SOURCE_TO_STALE_NAMES = {
    "email": ("email",),
    "calendar": ("calendar",),
    "messages_calls": ("messages", "calls"),
    "social": ("social",),
    "social_outbound": ("social_outbound",),
}


# Operator-facing refresh metadata per source. The intent (post-2026-05-18
# CoS grounding test) is that any stale-source warning the brief shows is
# paired with the exact command to fix it and an explicit caveat that a
# "no new signals here" conclusion is unreliable until refresh runs. The
# commands are intentionally local-only — they touch the operator's machine
# (Mac messages / call history, Google session) and should not be exposed
# to the Custom GPT.
REFRESH_SPECS = {
    "email": {
        "command": "python3 system/scripts/refresh_sources.py --email",
        "summary": (
            "Run the unified refresh wrapper. It normalizes raw_*.json per "
            "enabled email account, or prints the exact Cowork MCP command "
            "needed when raw input is missing."
        ),
    },
    "calendar": {
        "command": "python3 system/scripts/refresh_sources.py --calendar",
        "summary": (
            "Run the unified refresh wrapper. It normalizes raw_*.json per "
            "enabled calendar account, or prints the Cowork MCP command "
            "needed when raw input is missing."
        ),
    },
    "messages": {
        "command": "python3 system/scripts/refresh_sources.py --messages",
        "summary": "Refresh the macOS Messages feed (Mac-only, Full Disk Access required).",
    },
    "calls": {
        "command": "python3 system/scripts/refresh_sources.py --calls",
        "summary": "Refresh the macOS CallHistory feed (Mac-only, Full Disk Access required).",
    },
    "social": {
        "command": "python3 system/scripts/refresh_sources.py --social",
        "summary": (
            "Refresh the derived LinkedIn/social overlay caches for public "
            "posts, active-thread company mentions, market topics, and RI. "
            "Underlying system/inbox/social.feed.json must be repopulated "
            "separately (paste new posts, run a feed fetcher, or import a "
            "permissioned export/capture)."
        ),
    },
    "social_outbound": {
        "command": "python3 system/scripts/refresh_sources.py --social",
        "summary": (
            "Refresh derived engagement caches. system/inbox/social.own_posts.json "
            "and social.engagement.json must be repopulated separately. "
            "LinkedIn messaging exports/captures, when available, should also "
            "be scanned for RI, loop candidates, opportunities, and last-touch "
            "evidence."
        ),
    },
}


def _refresh_spec(name: str, age_h: float | None, threshold_h: int) -> dict:
    """Return the operator-facing refresh guidance for a source.

    Always emits the same shape so consumers (daily_brief, GPT) can render
    them uniformly. The `caveat` line states the operational consequence of
    trusting the source while stale — the interpretive guidance the operator
    needs after the 2026-05-18 grounding test. Age/threshold values are kept
    out of `caveat` so renderers can format them once without duplication."""
    spec = REFRESH_SPECS.get(name, {"command": None, "summary": None})
    return {
        "command": spec["command"],
        "summary": spec["summary"],
        "caveat": (
            f"Treat 'no new signals from {name}' as unreliable until this "
            f"source refreshes."
        ),
    }


def _apply_freshness_labels(signals: list[dict], stale_names: set[str]) -> None:
    """In-place: mark each signal `freshness=stale_source_limited` when its
    underlying source is in the stale set. Safe to call with empty stale set
    — it is a no-op."""
    if not stale_names:
        return
    for s in signals:
        names = _SOURCE_TO_STALE_NAMES.get(s.get("source"), ())
        if any(n in stale_names for n in names):
            s["freshness"] = "stale_source_limited"


# ---------------------------------------------------------------------------
# P-036: RI assessment contract
# ---------------------------------------------------------------------------
# relationship_signals.py is read-only compute — it never writes canonical
# state.  Therefore every signal carries status=proposed (candidate for
# passive_ri_ingest to act on), status=blocked (signal found but gated by
# confidence or staleness), or in degenerate cases status=unavailable.
# status=recorded and status=duplicate are reserved for passive_ri_ingest.py.

_CONFIDENCE_STALE_CAP = 0.55   # Per P-036: stale source max confidence
_CONFIDENCE_BLOCK_THRESHOLD = 0.40  # Below this → status=blocked

# Signal types that have a clear last_touch mutation target.
_TOUCH_SIGNAL_TYPES = {
    "direct_interaction", "inbound_warmth", "active_thread_movement",
    "meeting_completed", "active_thread_calendar_event",
    "engagement_on_own_post", "known_contact_post",
    "communication_failure_risk", "multi_channel_escalation",
}


def _ri_assessment_for_signal(sig: dict) -> dict:
    """Build a P-036 ri_assessment block for a relationship_signals signal.

    relationship_signals.py is read-only — status is always proposed or
    blocked, never recorded or duplicate.  passive_ri_ingest.py is
    responsible for promotion to recorded/duplicate.
    """
    raw_conf = sig.get("confidence", 0.5)
    freshness_tag = sig.get("freshness", "fresh")
    relevance = sig.get("strategic_relevance", "low")
    signal_type = sig.get("signal_type", "")
    contact_id = sig.get("contact_id")
    thread_ids = sig.get("active_thread_ids") or []
    evidence = sig.get("evidence") or []

    # Freshness → source_freshness label and confidence cap.
    if freshness_tag == "stale_source_limited":
        source_freshness = "stale"
        confidence = min(raw_conf, _CONFIDENCE_STALE_CAP)
    else:
        source_freshness = "fresh"
        confidence = raw_conf

    # Determine status.
    if source_freshness == "stale" and confidence < _CONFIDENCE_BLOCK_THRESHOLD:
        status = "blocked"
        block_reason = (
            f"source_freshness=stale; confidence={confidence:.2f} "
            f"below threshold {_CONFIDENCE_BLOCK_THRESHOLD}"
        )
    elif source_freshness == "stale":
        status = "blocked"
        block_reason = (
            f"source_freshness=stale; confidence capped at {_CONFIDENCE_STALE_CAP} "
            "per P-036; awaiting fresh source before proposing mutation"
        )
    elif confidence < _CONFIDENCE_BLOCK_THRESHOLD:
        status = "blocked"
        block_reason = (
            f"confidence={confidence:.2f} below threshold "
            f"{_CONFIDENCE_BLOCK_THRESHOLD}"
        )
    else:
        status = "proposed"
        block_reason = None

    # Proposed mutation (only when status=proposed and signal type has a known target).
    if status == "proposed" and signal_type in _TOUCH_SIGNAL_TYPES and contact_id:
        proposed_mutation = {
            "command": "touch",
            "payload": {
                "id": contact_id,
                "date": sig.get("event_at"),
                "note": f"[{signal_type}] {sig.get('reasoning', '')}".strip(),
            },
        }
    else:
        proposed_mutation = {"command": None, "payload": {}}

    # Display recommendation.
    if status == "blocked":
        if relevance in ("high", "medium"):
            # Blocked but decision-relevant — show when the block changes action.
            display_recommendation = "show"
            reason = (
                f"Signal blocked ({block_reason}); relevance={relevance} — "
                "surfaced so operator knows confidence is limited."
            )
        else:
            display_recommendation = "suppress_unless_asked"
            reason = (
                f"Signal blocked ({block_reason}); relevance={relevance} — "
                "suppressed unless operator asks for audit."
            )
    elif relevance == "high":
        display_recommendation = "show"
        reason = f"High-relevance {signal_type}; status=proposed — surface in brief."
    elif relevance == "medium":
        display_recommendation = "show"
        reason = f"Medium-relevance {signal_type}; status=proposed — surface in brief."
    else:
        display_recommendation = "suppress_unless_asked"
        reason = f"Low-relevance {signal_type}; status=proposed — suppress unless asked."

    return {
        "status": status,
        "source": "relationship_signals",
        "source_freshness": source_freshness,
        "confidence": round(confidence, 3),
        "evidence": [str(e) if not isinstance(e, str) else e for e in evidence[:5]],
        "mapped_contact_ids": [contact_id] if contact_id else [],
        "mapped_thread_ids": list(thread_ids),
        "proposed_mutation": proposed_mutation,
        "display_recommendation": display_recommendation,
        "reason": reason,
    }


def _attach_ri_assessments(signals: list[dict]) -> None:
    """In-place: attach a P-036 `ri_assessment` block to every signal.

    Called in build_report() after _apply_freshness_labels() so staleness
    state is already propagated before the assessment is computed.
    """
    for s in signals:
        s["ri_assessment"] = _ri_assessment_for_signal(s)


def _tier_strength(entry: dict | None) -> float:
    if not entry:
        return 0.30
    sc = entry.get("signal_class")
    tier = entry.get("rc_tier")
    return {
        ("RC", "inner"): 0.95,
        ("RC", "broader"): 0.80,
        ("RC", "dormant_valuable"): 0.65,
    }.get((sc, tier), {
        "RC": 0.70,
        "LKI": 0.55,
        "LMI": 0.35,
        "NPR": 0.20,
        "VC": 0.10,
    }.get(sc, 0.30))


def _relevance(strength: float, *, in_thread: bool, in_window: bool) -> str:
    if not in_window:
        return "low"
    if in_thread or strength >= 0.85:
        return "high"
    if strength >= 0.55:
        return "medium"
    return "low"


def _active_thread_ids_for_entry(entry: dict | None, threads: list[dict]) -> list[str]:
    """Find active threads tied to a matched direct-interaction contact."""
    if not entry:
        return []
    entry_id = entry.get("id")
    company = (entry.get("current_company") or "").lower()
    out = []
    for thread in threads:
        people = set(thread.get("people") or [])
        companies = " ".join(thread.get("companies") or []).lower()
        if entry_id and entry_id in people:
            out.append(thread.get("id"))
        elif company and company in companies:
            out.append(thread.get("id"))
    return [tid for tid in out if tid]


# ----------------------------------------------------------------------------
# Per-source signal extractors
# ----------------------------------------------------------------------------

def _email_signals(*, baseline, threads, window_start, window_end,
                   now) -> tuple[list[dict], dict, list[dict]]:
    """Returns (signals, source_state, reconciliation_prompts) for email."""
    overlay = core.email_overlay(baseline=baseline, threads=threads)
    fetched_at = overlay.get("fetched_at")
    fetched_dt = _parse_dt(fetched_at)
    age_h = _hours_ago(fetched_dt, now)
    stale_threshold = FRESHNESS_HOURS["email"]
    stale = (age_h is None) or (age_h > stale_threshold)
    signals: list[dict] = []
    recons: list[dict] = []

    # Threads from baseline-known senders
    for r in overlay.get("from_baseline") or []:
        msg_dt = _parse_dt(r.get("last_message_at"))
        if not _in_window(msg_dt, window_start, window_end):
            continue
        match = r.get("match") or {}
        # Pull the underlying baseline entry to compute tier strength precisely
        entry = next((e for e in baseline if e.get("id") == match.get("id")), None)
        in_thread = bool(r.get("matched_threads"))
        tier = _tier_strength(entry)
        subj = r.get("subject") or ""
        snippet_or_subj = (r.get("snippet") or "") + " " + subj
        # Classify subject signal type
        if JOB_RX.search(subj):
            sig_type = "job_opportunity"
            base_strength = 0.85
            action = (
                f"Review {match.get('name')}'s message thread `{subj[:60]}` against Todd's "
                f"target-role fit and active job threads. If aligned, prep response and "
                f"capture as an active thread or loop."
            )
        elif INTRO_RX.search(snippet_or_subj):
            sig_type = "intro_referral"
            base_strength = 0.80
            action = (
                f"Open or update an intro thread anchored on {match.get('name')}'s "
                f"message; confirm the bridge contact and target."
            )
        elif COLLAB_RX.search(snippet_or_subj):
            sig_type = "collaboration_opportunity"
            base_strength = 0.78
            action = (
                f"Treat {match.get('name')}'s message as a potential consulting/partnership "
                f"opening; draft a measured reply that explores fit before committing."
            )
        elif SCHEDULE_RX.search(snippet_or_subj):
            sig_type = "scheduling_opening"
            base_strength = 0.75
            action = (
                f"Reply to {match.get('name')} with a concrete proposed time and the "
                f"strategic framing to lead the next conversation with."
            )
        elif in_thread:
            sig_type = "active_thread_movement"
            base_strength = 0.82
            action = (
                f"Active thread `{','.join(r.get('matched_threads') or [])}` advanced via "
                f"{match.get('name')}; capture state change and decide next move today."
            )
        elif NOISE_SUBJECT_RX.search(subj):
            # Suppress noise even from baseline; fall through to no signal.
            continue
        else:
            sig_type = "inbound_warmth"
            base_strength = 0.55
            action = (
                f"Acknowledge {match.get('name')}'s message within the response window; "
                f"a measured reply preserves relationship warmth without overreach."
            )

        strength = min(1.0, base_strength * 0.6 + tier * 0.4 + (0.10 if in_thread else 0.0))
        rel = _relevance(strength, in_thread=in_thread, in_window=True)
        sig = _new_signal(
            contact_id=match.get("id"),
            name=match.get("name"),
            source="email",
            event_at=r.get("last_message_at"),
            signal_type=sig_type,
            signal_strength=strength,
            strategic_relevance=rel,
            recommended_action=action,
            reasoning=(
                f"{match.get('name')} ({match.get('signal_class')}/{match.get('rc_tier') or '—'}) "
                f"replied {(r.get('last_message_at') or '')[:10]}. "
                f"{'Open active thread match. ' if in_thread else ''}"
                f"Subject keyword class: {sig_type}."
            ),
            evidence=[{
                "source": "email_overlay.from_baseline",
                "thread_id": r.get("thread_id"),
                "subject": subj,
                "account_id": r.get("account_id"),
                "snippet": (r.get("snippet") or r.get("subject") or "")[:240],
            }],
            confidence=0.85 if in_thread else 0.70,
            active_thread_ids=r.get("matched_threads") or [],
        )
        signals.append(sig)

    # Active-thread company hits where the sender is NOT in baseline
    for r in overlay.get("active_thread_company_hits") or []:
        msg_dt = _parse_dt(r.get("last_message_at"))
        if not _in_window(msg_dt, window_start, window_end):
            continue
        sender = r.get("sender_email")
        companies = r.get("active_thread_company_hits") or []
        signals.append(_new_signal(
            contact_id=None,
            name=r.get("sender_name") or sender,
            source="email",
            event_at=r.get("last_message_at"),
            signal_type="active_thread_company_signal",
            signal_strength=0.72,
            strategic_relevance="high",
            recommended_action=(
                f"Sender `{sender}` is not yet in baseline but their message touches "
                f"active-thread companies ({', '.join(companies)}). Decide whether to "
                f"add the contact and create/extend the active thread."
            ),
            reasoning=(
                f"Email from unknown sender at company tied to active threads "
                f"{','.join(r.get('active_thread_ids') or [])}; subject `{(r.get('subject') or '')[:80]}`."
            ),
            evidence=[{
                "source": "email_overlay.active_thread_company_hits",
                "thread_id": r.get("thread_id"),
                "subject": r.get("subject"),
                "account_id": r.get("account_id"),
                "snippet": (r.get("snippet") or "")[:240],
                "company_hits": companies,
            }],
            confidence=0.65,
            active_thread_ids=r.get("active_thread_ids") or [],
            reconciliation_prompt=(
                f"Should `{sender}` be added to baseline and linked to the "
                f"{','.join(r.get('active_thread_ids') or [])} thread, treated as a "
                f"one-off contact, or ignored?"
            ),
        ))
        recons.append({
            "id": f"R-email-{r.get('thread_id')}",
            "entity_type": "contact",
            "entity_id": None,
            "prompt": (
                f"Email from `{sender}` (subject `{(r.get('subject') or '')[:80]}`) hits "
                f"active-thread companies {companies}. Add to baseline and link to "
                f"thread {','.join(r.get('active_thread_ids') or [])}?"
            ),
            "reason": "unknown_sender_touches_active_thread",
            "source": "email",
            "urgency": "medium",
            "confidence": 0.60,
            "recommended_mutation": "addContact + (optionally) openThread or thread context update",
            "safe_to_write": False,
        })

    # ----------------------------------------------------------------
    # Sent-followup signals (Step 3 of the canonical CoS sprint).
    #
    # Convert overlay.sent_followups into signals. The overlay already
    # classified each entry (active/warm/unknown × awaiting_response/
    # response_overdue); here we map those onto the signal contract and
    # decide when to surface them in the look-back window.
    #
    #   - response_overdue: always emit. The "today" state change is the
    #     window crossing, not the original send date.
    #   - awaiting_response: emit only if the original send falls within
    #     the look-back window (recent outbound worth surfacing today).
    #   - unknown / missing timestamp: skip — nothing to ground the
    #     signal at a point in time.
    # ----------------------------------------------------------------

    # Map overlay confidence labels onto numeric signal confidence so the
    # existing brief ordering and rendering work without changes.
    _SENT_CONF = {"high": 0.85, "medium": 0.70, "low": 0.55}
    # Per-status base signal strength. Overdue ranks above awaiting so
    # daily-brief sorting bubbles "follow up today" above "still waiting".
    _SENT_BASE_STRENGTH = {"response_overdue": 0.82, "awaiting_response": 0.62}

    for sf in overlay.get("sent_followups") or []:
        status = sf.get("response_status")
        if status not in _SENT_BASE_STRENGTH:
            continue
        sent_dt = _parse_dt(sf.get("sent_at"))
        if sent_dt is None:
            continue
        if status == "awaiting_response" and not _in_window(sent_dt, window_start, window_end):
            continue
        # `awaiting_response` inside the window or `response_overdue` at any age.

        matched_thread_ids = sf.get("matched_threads") or []
        matched_contacts = sf.get("matched_contacts") or []
        in_thread = bool(matched_thread_ids)
        category = sf.get("category") or "unknown"
        recipients = sf.get("to") or []
        primary_contact = matched_contacts[0] if matched_contacts else None
        # Prefer matched-baseline name; fall back to first recipient's
        # name, then email; default to a generic placeholder.
        if primary_contact and primary_contact.get("name"):
            recipient_label = primary_contact["name"]
        elif recipients:
            first = recipients[0] or {}
            recipient_label = first.get("name") or first.get("email") or "(recipient)"
        else:
            recipient_label = "(recipient)"

        if status == "response_overdue":
            rel = "high"
            action = (
                f"Follow up with {recipient_label} on `{(sf.get('subject') or '')[:60]}` "
                f"today — expected-response window has passed "
                f"({sf.get('business_days_since_sent')} business days since sent; "
                f"window {sf.get('response_window_business_days')} business days). "
                f"If the lane is dead, capture that decision; do not infer rejection from silence."
            )
        else:  # awaiting_response, inside window
            rel = "high" if in_thread else ("medium" if category == "warm" else "low")
            action = (
                f"Monitor {recipient_label} on `{(sf.get('subject') or '')[:60]}` — "
                f"sent {sf.get('business_days_since_sent')} business days ago; "
                f"expected by {sf.get('expected_response_by')}. "
                f"No additional follow-up until the window closes."
            )

        base_strength = _SENT_BASE_STRENGTH[status]
        # Boost: in-thread match adds the same +0.10 the inbound path uses.
        strength = min(1.0, base_strength + (0.10 if in_thread else 0.0))

        signals.append(_new_signal(
            contact_id=(primary_contact or {}).get("id"),
            name=recipient_label,
            source="email",
            event_at=sf.get("sent_at"),
            signal_type=status,  # "awaiting_response" or "response_overdue"
            signal_strength=strength,
            strategic_relevance=rel,
            recommended_action=action,
            reasoning=(
                f"Todd was the most recent sender on `{(sf.get('subject') or '')[:80]}` "
                f"(sent {(sf.get('sent_at') or '')[:10]}, "
                f"{sf.get('business_days_since_sent')} business days ago, "
                f"category={category}, "
                f"window={sf.get('response_window_business_days')} business days). "
                f"Status from overlay: {status}. "
                f"{'In-thread match: ' + ','.join(matched_thread_ids) + '. ' if in_thread else ''}"
                f"Silence is not rejection unless explicit rejection evidence exists."
            ),
            evidence=[{
                "source": "email_overlay.sent_followups",
                "thread_id": sf.get("thread_id"),
                "subject": sf.get("subject"),
                "account_id": sf.get("account_id"),
                "sent_at": sf.get("sent_at"),
                "to": recipients,
                "category": category,
                "response_window_business_days": sf.get("response_window_business_days"),
                "business_days_since_sent": sf.get("business_days_since_sent"),
                "expected_response_by": sf.get("expected_response_by"),
            }],
            confidence=_SENT_CONF.get(sf.get("confidence"), 0.60),
            active_thread_ids=matched_thread_ids,
        ))

    source_state = {
        "name": "email",
        "fetched_at": fetched_at,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "threshold_hours": stale_threshold,
        "stale": stale,
        "accounts_seen": overlay.get("accounts_seen") or [],
        "recommendation": (
            "Run the email fetcher (fetch_via_session.py or fetch_google.py) or paste "
            "high-signal threads manually."
        ) if stale else None,
        "recommended_refresh": _refresh_spec("email", age_h, stale_threshold) if stale else None,
    }
    return signals, source_state, recons


def _calendar_signals(*, baseline, threads, today, window_start, window_end,
                      now) -> tuple[list[dict], dict, list[dict]]:
    overlay = core.calendar_overlay(today, baseline=baseline, threads=threads)
    fetched_at = overlay.get("fetched_at")
    fetched_dt = _parse_dt(fetched_at)
    age_h = _hours_ago(fetched_dt, now)
    stale_threshold = FRESHNESS_HOURS["calendar"]
    stale = (age_h is None) or (age_h > stale_threshold)
    signals: list[dict] = []
    recons: list[dict] = []

    # Pull today + tomorrow + this_week buckets; filter to window
    buckets = ("today", "tomorrow", "this_week")
    all_events = []
    for k in buckets:
        for ev in overlay.get(k) or []:
            all_events.append((k, ev))

    for bucket, ev in all_events:
        start_dt = _parse_dt(ev.get("start"))
        # Calendar signals: count anything starting in the look-back window OR
        # starting in the next 24h since both are operator-relevant today.
        forward_horizon = now + timedelta(hours=24)
        if start_dt is None:
            continue
        relevant = _in_window(start_dt, window_start, forward_horizon)
        if not relevant:
            continue
        matched = [a for a in (ev.get("attendees_matched") or []) if a.get("id")]
        company_hits = ev.get("active_thread_company_hits") or []
        thread_ids = ev.get("matched_threads") or []
        in_thread = bool(thread_ids)
        # Classify
        if start_dt < now and matched:
            sig_type = "meeting_completed"
            base_strength = 0.78
            action = (
                "Meeting recently passed — capture outcome, next step, names, "
                "timing, and follow-up loop before context cools."
            )
        elif start_dt >= now and start_dt <= now + timedelta(hours=24) and matched:
            sig_type = "meeting_today_or_imminent"
            base_strength = 0.85
            action = "Prep the meeting: prior thread context, current loops, target outcome."
        elif company_hits and not matched:
            sig_type = "active_thread_calendar_event"
            base_strength = 0.70
            action = (
                f"Calendar event touches active-thread companies ({', '.join(company_hits)}). "
                f"Confirm strategic framing and capture as a thread event."
            )
        else:
            continue
        # Aggregate tier strength across matched attendees
        if matched:
            tier_max = max((_tier_strength(
                next((e for e in baseline if e.get("id") == m.get("id")), None)
            ) for m in matched), default=0.40)
        else:
            tier_max = 0.40
        strength = min(1.0, base_strength * 0.6 + tier_max * 0.4 + (0.10 if in_thread else 0.0))
        rel = _relevance(strength, in_thread=in_thread, in_window=True)
        primary = matched[0] if matched else None
        signals.append(_new_signal(
            contact_id=primary.get("id") if primary else None,
            name=primary.get("name") if primary else ev.get("title"),
            source="calendar",
            event_at=ev.get("start"),
            signal_type=sig_type,
            signal_strength=strength,
            strategic_relevance=rel,
            recommended_action=action,
            reasoning=(
                f"{bucket.title()} event `{ev.get('title')}` at {(ev.get('start') or '')[:16]} "
                f"with {len(matched)} matched attendee(s), "
                f"{'in active thread, ' if in_thread else ''}company hits: "
                f"{company_hits or 'none'}."
            ),
            evidence=[{
                "source": "calendar_overlay",
                "event_id": ev.get("id"),
                "title": ev.get("title"),
                "start": ev.get("start"),
                "account_id": ev.get("account_id"),
                "matched_attendees": [
                    {"id": m.get("id"), "name": m.get("name")} for m in matched
                ],
                "active_thread_company_hits": company_hits,
            }],
            confidence=0.85 if matched else 0.55,
            active_thread_ids=thread_ids,
        ))
        # Reconciliation for unmatched attendees on important events
        if (in_thread or company_hits) and ev.get("unmatched_count"):
            recons.append({
                "id": f"R-cal-{ev.get('id')}",
                "entity_type": "calendar_event",
                "entity_id": ev.get("id"),
                "prompt": (
                    f"Event `{ev.get('title')}` ({(ev.get('start') or '')[:10]}) has "
                    f"{ev.get('unmatched_count')} attendee(s) not in baseline and touches "
                    f"{'thread ' + ','.join(thread_ids) if thread_ids else 'active-thread companies'}. "
                    f"Promote any of them to baseline?"
                ),
                "reason": "unmatched_attendees_on_strategic_event",
                "source": "calendar",
                "urgency": "medium",
                "confidence": 0.65,
                "recommended_mutation": "addContact (one or more)",
                "safe_to_write": False,
            })

    source_state = {
        "name": "calendar",
        "fetched_at": fetched_at,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "threshold_hours": stale_threshold,
        "stale": stale,
        "accounts_seen": overlay.get("accounts_seen") or [],
        "recommendation": "Refresh calendar feed." if stale else None,
        "recommended_refresh": _refresh_spec("calendar", age_h, stale_threshold) if stale else None,
    }
    return signals, source_state, recons


def _interaction_signals(*, baseline, threads, today, now, window_start,
                         window_end) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (signals, source_states, recons) for messages + calls."""
    # Use the existing overlay for matched contacts but re-filter to the window
    overlay = core.interaction_overlay(baseline=baseline, today=today, recent_days=7)
    msg_payload = core.load_messages()
    call_payload = core.load_calls()
    msg_fetched = msg_payload.get("fetched_at")
    call_fetched = call_payload.get("fetched_at")

    msg_age = _hours_ago(_parse_dt(msg_fetched), now)
    call_age = _hours_ago(_parse_dt(call_fetched), now)

    _msg_stale = (msg_age is None) or (msg_age > FRESHNESS_HOURS["messages"])
    _call_stale = (call_age is None) or (call_age > FRESHNESS_HOURS["calls"])
    msg_state = {
        "name": "messages",
        "fetched_at": msg_fetched,
        "age_hours": round(msg_age, 1) if msg_age is not None else None,
        "threshold_hours": FRESHNESS_HOURS["messages"],
        "stale": _msg_stale,
        "recommendation": "Run fetch_apple_messages.py to refresh." if _msg_stale else None,
        "recommended_refresh": (
            _refresh_spec("messages", msg_age, FRESHNESS_HOURS["messages"])
            if _msg_stale else None
        ),
    }
    call_state = {
        "name": "calls",
        "fetched_at": call_fetched,
        "age_hours": round(call_age, 1) if call_age is not None else None,
        "threshold_hours": FRESHNESS_HOURS["calls"],
        "stale": _call_stale,
        "recommendation": "Run fetch_apple_calls.py to refresh." if _call_stale else None,
        "recommended_refresh": (
            _refresh_spec("calls", call_age, FRESHNESS_HOURS["calls"])
            if _call_stale else None
        ),
    }

    signals: list[dict] = []
    recons: list[dict] = []

    # Re-walk events directly to keep only those inside the window
    by_contact: dict[str, dict] = {}
    phone_index = core._build_phone_index(baseline)
    email_index = core._build_email_index(baseline)

    def _bump(entry: dict, ev: dict, kind: str):
        slot = by_contact.setdefault(entry["id"], {
            "id": entry["id"], "name": entry["name"],
            "signal_class": entry.get("signal_class"),
            "rc_tier": entry.get("rc_tier"),
            "current_company": entry.get("current_company"),
            "current_last_touch": entry.get("last_touch"),
            "msg_in": 0, "msg_out": 0,
            "call_in": 0, "call_out": 0, "call_missed": 0,
            "last_at": None,
            "message_snippets": [],
        })
        if kind == "message":
            if ev.get("direction") == "outbound":
                slot["msg_out"] += 1
            else:
                slot["msg_in"] += 1
            snippet = ev.get("snippet")
            if snippet and snippet not in slot["message_snippets"]:
                slot["message_snippets"].append(str(snippet)[:240])
        else:
            d = ev.get("direction") or ""
            if d == "outbound":
                slot["call_out"] += 1
            elif d == "missed":
                slot["call_missed"] += 1
            else:
                slot["call_in"] += 1
        at = ev.get("at")
        if at and at > (slot["last_at"] or ""):
            slot["last_at"] = at

    for ev in msg_payload.get("events") or []:
        dt = _parse_dt(ev.get("at"))
        if not _in_window(dt, window_start, window_end):
            continue
        entry = core._match_handle(ev.get("handle") or "", phone_index, email_index)
        if entry:
            _bump(entry, ev, "message")
    for ev in call_payload.get("events") or []:
        dt = _parse_dt(ev.get("at"))
        if not _in_window(dt, window_start, window_end):
            continue
        entry = core._match_handle(ev.get("handle") or "", phone_index, email_index)
        if entry:
            _bump(entry, ev, "call")

    for slot in by_contact.values():
        total_events = (slot["msg_in"] + slot["msg_out"]
                        + slot["call_in"] + slot["call_out"] + slot["call_missed"])
        entry = next((e for e in baseline if e.get("id") == slot["id"]), None)
        if _is_nontracked_personal_contact(entry):
            continue
        tier = _tier_strength(entry)
        thread_ids = _active_thread_ids_for_entry(entry, threads)
        in_thread = bool(thread_ids)
        # Strength rises with volume but saturates fast
        base_strength = min(1.0, 0.40 + 0.10 * total_events)
        strength = min(1.0, base_strength * 0.6 + tier * 0.4 + (0.10 if in_thread else 0.0))
        rel = _relevance(strength, in_thread=in_thread, in_window=True)
        last_at = slot["last_at"]
        snippet_text = " ".join(slot.get("message_snippets") or [])
        communication_failure = bool(COMMUNICATION_FAILURE_RX.search(snippet_text))
        channel_escalation = bool(CHANNEL_ESCALATION_RX.search(snippet_text))
        signal_type = (
            "communication_failure_risk" if communication_failure else
            "multi_channel_escalation" if channel_escalation and in_thread else
            "direct_interaction"
        )
        action_bits = []
        if communication_failure:
            action_bits.append(
                f"treat {slot['name']}'s message as an active-opportunity communication risk: verify email restoration, reply through the successful channel, and acknowledge the failed delivery"
            )
        elif channel_escalation and in_thread:
            action_bits.append(
                f"treat {slot['name']}'s channel switch as elevated engagement; preserve momentum on the active thread today"
            )
        if slot["call_missed"]:
            action_bits.append(f"return the missed call to {slot['name']}")
        if slot["msg_in"] and not slot["msg_out"]:
            action_bits.append(f"reply to {slot['name']}'s message thread")
        if not action_bits:
            action_bits.append(f"capture the interaction as a `last_touch` update for {slot['name']}")
        signals.append(_new_signal(
            contact_id=slot["id"],
            name=slot["name"],
            source="messages_calls",
            event_at=last_at,
            signal_type=signal_type,
            signal_strength=strength,
            strategic_relevance=rel,
            recommended_action="; ".join(action_bits) + ".",
            reasoning=(
                f"Direct interaction with {slot['name']} "
                f"({slot['signal_class']}/{slot['rc_tier'] or '—'}) inside the window: "
                f"{slot['msg_in']}/{slot['msg_out']} msg in/out, "
                f"{slot['call_in']}/{slot['call_out']}/{slot['call_missed']} "
                f"calls in/out/missed. Last event {last_at}. "
                f"{'Matched active thread(s): ' + ','.join(thread_ids) + '. ' if in_thread else ''}"
                f"{'Message content indicates bounced/failed email or infrastructure issue. ' if communication_failure else ''}"
                f"{'Message content indicates channel escalation. ' if channel_escalation else ''}"
            ),
            evidence=[{
                "source": "messages_calls_overlay",
                "messages_in": slot["msg_in"], "messages_out": slot["msg_out"],
                "calls_in": slot["call_in"], "calls_out": slot["call_out"],
                "calls_missed": slot["call_missed"],
                "last_interaction_at": last_at,
                "message_snippets": slot.get("message_snippets") or [],
                "communication_failure_detected": communication_failure,
                "channel_escalation_detected": channel_escalation,
            }],
            confidence=0.92 if communication_failure and in_thread else 0.90,
            active_thread_ids=thread_ids,
        ))
        # Reconciliation: should last_touch advance?
        last_day = (last_at or "")[:10]
        cur = slot["current_last_touch"]
        if last_day and (not cur or last_day > cur):
            recons.append({
                "id": f"R-touch-{slot['id']}",
                "entity_type": "contact",
                "entity_id": slot["id"],
                "prompt": (
                    f"Direct interaction with {slot['name']} on {last_day} is newer than "
                    f"recorded `last_touch` ({cur or 'none'}). Should RB update last_touch?"
                ),
                "reason": "interaction_newer_than_recorded_last_touch",
                "source": "messages_calls",
                "urgency": "low" if (slot["signal_class"] != "RC") else "medium",
                "confidence": 0.85,
                "recommended_mutation": f"touchContact id={slot['id']} date={last_day}",
                "safe_to_write": True,
            })

    return signals, [msg_state, call_state], recons


def _social_signals(*, baseline, threads, today, now,
                    window_start, window_end) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (signals, source_states, recons) for inbound social feed and
    outbound engagement on own posts."""
    feed = core.social_overlay(baseline=baseline, threads=threads, recent_days=7)
    out_overlay = core.social_outbound_overlay(baseline=baseline, threads=threads, today=today)

    feed_fetched = feed.get("fetched_at")
    feed_age = _hours_ago(_parse_dt(feed_fetched), now)
    out_fetched = out_overlay.get("fetched_at")
    out_age = _hours_ago(_parse_dt(out_fetched), now)

    _feed_stale = (feed_age is None) or (feed_age > FRESHNESS_HOURS["social"])
    _out_stale = (out_age is None) or (out_age > FRESHNESS_HOURS["social_outbound"])
    feed_state = {
        "name": "social",
        "fetched_at": feed_fetched,
        "age_hours": round(feed_age, 1) if feed_age is not None else None,
        "threshold_hours": FRESHNESS_HOURS["social"],
        "stale": _feed_stale,
        "recommendation": "Refresh social.feed.json (paste new posts or run a fetcher)." if _feed_stale else None,
        "recommended_refresh": (
            _refresh_spec("social", feed_age, FRESHNESS_HOURS["social"])
            if _feed_stale else None
        ),
    }
    out_state = {
        "name": "social_outbound",
        "fetched_at": out_fetched,
        "age_hours": round(out_age, 1) if out_age is not None else None,
        "threshold_hours": FRESHNESS_HOURS["social_outbound"],
        "stale": _out_stale,
        "recommendation": "Refresh own-posts + engagement files." if _out_stale else None,
        "recommended_refresh": (
            _refresh_spec("social_outbound", out_age, FRESHNESS_HOURS["social_outbound"])
            if _out_stale else None
        ),
    }

    signals: list[dict] = []
    recons: list[dict] = []

    # Inbound: posts from known contacts within window
    for r in feed.get("from_baseline") or []:
        dt = _parse_dt(r.get("posted_at"))
        if not _in_window(dt, window_start, window_end):
            continue
        match = r.get("match") or {}
        entry = next((e for e in baseline if e.get("id") == match.get("id")), None)
        tier = _tier_strength(entry)
        in_thread = bool(r.get("matched_threads"))
        strength = min(1.0, 0.55 + tier * 0.35 + (0.10 if in_thread else 0.0))
        rel = _relevance(strength, in_thread=in_thread, in_window=True)
        signals.append(_new_signal(
            contact_id=match.get("id"),
            name=match.get("name"),
            source="social",
            event_at=r.get("posted_at"),
            signal_type="known_contact_post",
            signal_strength=strength,
            strategic_relevance=rel,
            recommended_action=(
                f"Decide whether engaging with {match.get('name')}'s post (comment, "
                f"reshare, DM) is the right relationship move today."
            ),
            reasoning=(
                f"{match.get('name')} ({match.get('signal_class')}/"
                f"{match.get('rc_tier') or '—'}) posted on {(r.get('posted_at') or '')[:10]}. "
                f"{'Touches active thread. ' if in_thread else ''}"
                f"Engagement: {r.get('engagement') or {}}."
            ),
            evidence=[{
                "source": "social_overlay.from_baseline",
                "post_id": r.get("id"),
                "text": (r.get("text") or "")[:240],
                "post_url": r.get("post_url"),
                "active_thread_company_hits": r.get("active_thread_company_hits"),
            }],
            confidence=0.80,
            active_thread_ids=r.get("matched_threads") or [],
        ))

    # Inbound: posts from non-baseline authors hitting active-thread companies
    for r in feed.get("active_thread_company_hits") or []:
        dt = _parse_dt(r.get("posted_at"))
        if not _in_window(dt, window_start, window_end):
            continue
        signals.append(_new_signal(
            contact_id=None,
            name=r.get("author_name"),
            source="social",
            event_at=r.get("posted_at"),
            signal_type="active_thread_social_signal",
            signal_strength=0.65,
            strategic_relevance="medium",
            recommended_action=(
                f"Unknown author `{r.get('author_name')}` posted about "
                f"{', '.join(r.get('active_thread_company_hits') or [])}. Check whether "
                f"they should enter baseline or whether the content informs the thread."
            ),
            reasoning=(
                f"Active-thread company `{', '.join(r.get('active_thread_company_hits') or [])}` "
                f"surfaced in a post from non-baseline author."
            ),
            evidence=[{
                "source": "social_overlay.active_thread_company_hits",
                "post_id": r.get("id"),
                "text": (r.get("text") or "")[:240],
                "author_url": r.get("author_url"),
            }],
            confidence=0.55,
            active_thread_ids=r.get("active_thread_ids") or [],
            reconciliation_prompt=(
                f"Add `{r.get('author_name')}` to baseline given the active-thread relevance?"
            ),
        ))

    # Outbound: engagement events from known contacts on Todd's own posts
    for r in out_overlay.get("active_thread_engagement") or []:
        dt = _parse_dt(r.get("at"))
        if not _in_window(dt, window_start, window_end):
            continue
        entry = next((e for e in baseline if e.get("id") == r.get("engager_id")), None)
        tier = _tier_strength(entry)
        strength = min(1.0, 0.50 + tier * 0.40)
        signals.append(_new_signal(
            contact_id=r.get("engager_id"),
            name=r.get("engager_name"),
            source="social_outbound",
            event_at=r.get("at"),
            signal_type="engagement_on_own_post",
            signal_strength=strength,
            strategic_relevance=_relevance(strength, in_thread=True, in_window=True),
            recommended_action=(
                f"{r.get('engager_name')} engaged ({r.get('type')}) on a post touching "
                f"{', '.join(r.get('company_hits') or [])}. Use the opening to advance the "
                f"thread — DM, comment-reply, or specific ask."
            ),
            reasoning=(
                f"Engagement event from known contact on a post touching active-thread "
                f"company. Tier strength {tier:.2f}."
            ),
            evidence=[{
                "source": "social_outbound_overlay.active_thread_engagement",
                "post_id": r.get("post_id"),
                "type": r.get("type"),
                "company_hits": r.get("company_hits"),
            }],
            confidence=0.85,
            active_thread_ids=r.get("active_thread_ids") or [],
        ))

    return signals, [feed_state, out_state], recons


# ----------------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------------

def _what_to_ignore(*, baseline, threads, window_start, window_end) -> list[dict]:
    """Return a short list of items RB looked at and chose NOT to surface, with
    a one-line reason each. The Custom GPT can paraphrase this into a
    'what to ignore' section so the operator sees that the suppression
    happened on purpose."""
    overlay = core.email_overlay(baseline=baseline, threads=threads)
    out: list[dict] = []
    noise = overlay.get("noise_skipped") or 0
    selfs = overlay.get("self_sent_skipped") or 0
    if noise:
        out.append({
            "category": "email_noise",
            "count": noise,
            "reason": "Newsletter / no-reply / notification senders suppressed by domain pattern.",
        })
    if selfs:
        out.append({
            "category": "email_self_sent",
            "count": selfs,
            "reason": (
                "Threads where Todd was the most recent sender but the overlay "
                "could not anchor the thread to an active thread, baseline "
                "contact, or recipient. Anchored self-sent threads are now "
                "first-class signals (followup_sent / awaiting_response / "
                "response_overdue) — see overlay.sent_followups."
            ),
        })
    # Suppress baseline contacts whose newest email is a known noise subject
    suppressed = 0
    for r in overlay.get("from_baseline") or []:
        if NOISE_SUBJECT_RX.search(r.get("subject") or ""):
            suppressed += 1
    if suppressed:
        out.append({
            "category": "email_subject_noise",
            "count": suppressed,
            "reason": "Baseline-sender threads whose subjects match a known noise pattern.",
        })
    return out


SEEN_CACHE_PATH = core.CACHE_DIR / "relationship_signals_seen.json"
SEEN_TTL_DAYS = 14


def _signal_fingerprint(sig: dict) -> str:
    """Stable identity for a signal across runs — used to detect repeats when
    the look-back window is widened (RB-9.66-D)."""
    return "|".join(str(x) for x in (
        sig.get("contact_id") or sig.get("name") or "",
        sig.get("source") or "",
        sig.get("signal_type") or "",
        (sig.get("event_at") or "")[:16],
    ))


def _load_seen_fingerprints() -> dict:
    if SEEN_CACHE_PATH.exists():
        try:
            return json.loads(SEEN_CACHE_PATH.read_text(encoding="utf-8")).get("fingerprints") or {}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_seen_fingerprints(fingerprints: dict, today: date) -> None:
    cutoff = (today - timedelta(days=SEEN_TTL_DAYS)).isoformat()
    pruned = {fp: seen for fp, seen in fingerprints.items() if seen >= cutoff}
    SEEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_CACHE_PATH.write_text(
        json.dumps({"_generated_at": datetime.now(timezone.utc).isoformat(),
                     "fingerprints": pruned}, indent=2) + "\n",
        encoding="utf-8",
    )


def _apply_novelty_labels(signals: list[dict], today: date) -> dict:
    """RB-9.66-D: mark each signal `previously_surfaced` based on a persisted
    fingerprint cache, so widening the look-back window doesn't make the brief
    repeat the same relationship signal on consecutive days. Returns updated
    fingerprint map (caller persists via --cache)."""
    seen = _load_seen_fingerprints()
    today_iso = today.isoformat()
    for sig in signals:
        fp = _signal_fingerprint(sig)
        sig["previously_surfaced"] = fp in seen
        seen.setdefault(fp, today_iso)
    return seen


def build_report(today: date | None = None, hours: int = 24) -> dict:
    """Build the relationship-signals report dict for a given anchor date and
    window length. Default window: last 24 hours."""
    if today is None:
        today = date.today()
    now = datetime.now(timezone.utc)
    # Anchor the window to "now" so a same-day fetch shows recency correctly.
    # The `today` arg only controls calendar bucketing.
    window_end = now
    window_start = now - timedelta(hours=hours)

    baseline = core.load_baseline()
    open_threads = [t for t in core.load_active_threads() if t.get("status") == "open"]

    email_sigs, email_state, email_recons = _email_signals(
        baseline=baseline, threads=open_threads,
        window_start=window_start, window_end=window_end, now=now,
    )
    cal_sigs, cal_state, cal_recons = _calendar_signals(
        baseline=baseline, threads=open_threads, today=today,
        window_start=window_start, window_end=window_end, now=now,
    )
    int_sigs, int_states, int_recons = _interaction_signals(
        baseline=baseline, threads=open_threads, today=today, now=now,
        window_start=window_start, window_end=window_end,
    )
    soc_sigs, soc_states, soc_recons = _social_signals(
        baseline=baseline, threads=open_threads, today=today, now=now,
        window_start=window_start, window_end=window_end,
    )

    excluded_contact_ids = {
        e.get("id") for e in baseline
        if e.get("id") and _is_nontracked_personal_contact(e)
    }
    signals = [
        s for s in (email_sigs + cal_sigs + int_sigs + soc_sigs)
        if s.get("contact_id") not in excluded_contact_ids
    ]

    # Sort signals deterministically: high → medium → low, then strength desc,
    # then event_at desc.
    signals.sort(key=lambda s: (
        RELEVANCE_RANK.get(s.get("strategic_relevance"), 9),
        -s.get("signal_strength", 0.0),
        -(int(_parse_dt(s.get("event_at")).timestamp()) if _parse_dt(s.get("event_at")) else 0),
    ))

    # Reconciliation list — high urgency first, then medium, then low.
    urgency_rank = {"high": 0, "medium": 1, "low": 2}
    reconciliation = [
        r for r in (email_recons + cal_recons + int_recons + soc_recons)
        if r.get("entity_id") not in excluded_contact_ids
    ]
    reconciliation.sort(key=lambda r: urgency_rank.get(r.get("urgency"), 9))

    # Stale-source warnings — only those marked stale
    source_states = [email_state, cal_state] + int_states + soc_states
    stale_sources = [s for s in source_states if s.get("stale")]

    # Propagate stale-source state down to each signal as `freshness`. The
    # signal's `grounding` is unchanged (still system_detected for source
    # observations) — the freshness label tells the operator that a "no
    # signal here" conclusion is unreliable because the feed is stale.
    _apply_freshness_labels(
        signals,
        stale_names={s.get("name") for s in stale_sources if s.get("name")},
    )

    # P-036: attach ri_assessment contract block to every signal.
    # Must run after _apply_freshness_labels so staleness is already set.
    _attach_ri_assessments(signals)

    # RB-9.66-D: mark previously-surfaced signals so a widened look-back
    # window doesn't repeat the same item across consecutive briefs. The
    # updated fingerprint map is returned for the caller to persist via
    # --cache; without --cache nothing is written (read-only by default).
    seen_fingerprints = _apply_novelty_labels(signals, today)

    # Tag reconciliation prompts as `inferred` — RB synthesizes these from
    # near-misses (signal present, certainty absent).
    for r in reconciliation:
        r.setdefault("grounding", "inferred")

    ignored = _what_to_ignore(
        baseline=baseline, threads=open_threads,
        window_start=window_start, window_end=window_end,
    )
    # `what_to_ignore` items are suppression decisions, not source events —
    # label them as `inferred` so consumers do not treat them as detected.
    for w in ignored:
        w.setdefault("grounding", "inferred")

    return {
        "today": today.isoformat(),
        "window_hours": hours,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "active_threads": [
            {"id": t.get("id"), "title": t.get("title"),
             "boost_for_brief": t.get("boost_for_brief")}
            for t in open_threads
        ],
        "signals": signals,
        "signal_counts_by_relevance": {
            "high": sum(1 for s in signals if s["strategic_relevance"] == "high"),
            "medium": sum(1 for s in signals if s["strategic_relevance"] == "medium"),
            "low": sum(1 for s in signals if s["strategic_relevance"] == "low"),
        },
        "signal_counts_by_source": {
            src: sum(1 for s in signals if s["source"] == src)
            for src in ("email", "calendar", "messages_calls", "social", "social_outbound")
        },
        "signal_counts_by_grounding": {
            g: sum(1 for s in signals if s.get("grounding") == g)
            for g in ("system_detected", "inferred", "manual_user_provided",
                      "stale_source_limited")
        },
        "signal_counts_by_novelty": {
            "new": sum(1 for s in signals if not s.get("previously_surfaced")),
            "repeat": sum(1 for s in signals if s.get("previously_surfaced")),
        },
        "_seen_fingerprints": seen_fingerprints,
        "signal_counts_by_freshness": {
            f: sum(1 for s in signals if s.get("freshness") == f)
            for f in ("fresh", "stale_source_limited")
        },
        # P-036: assessment status summary across all signals.
        "ri_assessment_summary": {
            st: sum(
                1 for s in signals
                if (s.get("ri_assessment") or {}).get("status") == st
            )
            for st in ("proposed", "blocked", "irrelevant", "unavailable")
        },
        "grounding_legend": {
            "system_detected": "Observed directly in a connected source within the window.",
            "inferred": "Synthesized by RB from rules or near-misses; not a direct observation.",
            "manual_user_provided": "Sourced from operator-typed state (active_threads.yaml, RC cards).",
            "stale_source_limited": "Source feed is past its freshness threshold; conclusions partial.",
        },
        "reconciliation_needed": reconciliation,
        "stale_sources": stale_sources,
        "source_states": source_states,
        "what_to_ignore": ignored,
    }


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def render_text(report: dict) -> str:
    out: list[str] = []
    out.append(f"# Relationship signals — last {report['window_hours']}h "
               f"({report['window_start'][:16]} → {report['window_end'][:16]})\n")
    cnt = report["signal_counts_by_relevance"]
    out.append(f"High: {cnt['high']} · Medium: {cnt['medium']} · Low: {cnt['low']}. "
               f"Reconciliation prompts: {len(report['reconciliation_needed'])}. "
               f"Stale sources: {len(report['stale_sources'])}.\n")
    if report["stale_sources"]:
        out.append("## Stale-source warnings\n")
        for s in report["stale_sources"]:
            age = s.get("age_hours")
            age_label = f"{age}h stale" if age is not None else "never fetched"
            rec_block = s.get("recommended_refresh") or {}
            caveat = rec_block.get("caveat") or s.get("recommendation") or ""
            cmd = rec_block.get("command")
            out.append(
                f"- **{s['name']}**: {age_label}; threshold {s['threshold_hours']}h"
            )
            if caveat:
                out.append(f"  - {caveat}")
            if cmd:
                out.append(f"  - refresh: `{cmd}`")
        out.append("")
    out.append("## Signals\n")
    if not report["signals"]:
        out.append("(no signals in window)\n")
    for s in report["signals"]:
        out.append(f"### [{s['strategic_relevance'].upper()}] {s['signal_type']} — "
                   f"{s.get('name') or s.get('contact_id') or 'unknown'} "
                   f"({s['source']}, strength {s['signal_strength']:.2f}, "
                   f"confidence {s['confidence']:.2f})")
        out.append(
            f"- grounding: `{s.get('grounding', 'system_detected')}` · "
            f"freshness: `{s.get('freshness', 'fresh')}`"
        )
        out.append(f"- event_at: `{s.get('event_at')}`")
        if s.get("active_thread_ids"):
            out.append(f"- active threads: {', '.join(s['active_thread_ids'])}")
        out.append(f"- reasoning: {s['reasoning']}")
        out.append(f"- recommended action: {s['recommended_action']}")
        if s.get("reconciliation_prompt"):
            out.append(f"- reconciliation: {s['reconciliation_prompt']}")
        out.append("")
    if report["reconciliation_needed"]:
        out.append("## Reconciliation needed\n")
        for r in report["reconciliation_needed"]:
            out.append(f"- **[{r['urgency']}]** {r['prompt']}")
            out.append(f"  recommended_mutation: `{r['recommended_mutation']}` "
                       f"(safe_to_write: {r['safe_to_write']})")
        out.append("")
    if report["what_to_ignore"]:
        out.append("## What RB ignored\n")
        for w in report["what_to_ignore"]:
            out.append(f"- {w['category']} ({w['count']}): {w['reason']}")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _smoke_sent_followups() -> int:
    """In-memory regression for the sent-followup signal types (Step 3).

    Patches core.email_overlay with a synthetic overlay that contains
    sent_followups entries spanning the cases that matter:
      - response_overdue, active match (must surface, high relevance);
      - awaiting_response, in-window, warm baseline (must surface);
      - awaiting_response, OUT-of-window (must be suppressed);
      - unknown status / missing timestamp (must be suppressed).
    """
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    # Anchor "now" on a midweek timestamp.
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
    window_start = now - timedelta(hours=24)
    window_end = now

    overdue_sent_at = (now - timedelta(days=10)).isoformat()
    in_window_sent_at = (now - timedelta(hours=4)).isoformat()
    out_of_window_sent_at = (now - timedelta(days=4)).isoformat()

    fake_overlay = {
        "fetched_at": now.isoformat(),
        "stale": False,
        "accounts_seen": ["primary"],
        "from_baseline": [],
        "active_thread_company_hits": [],
        "senders_not_in_baseline": [],
        "noise_skipped": 0,
        "self_sent_skipped": 0,
        "sent_followups": [
            {  # (1) overdue + in-thread: must surface with high relevance
                "thread_id": "T-A",
                "subject": "Re: Harri intro follow-up",
                "sent_at": overdue_sent_at,
                "account_id": "primary",
                "account_label": "Primary",
                "to": [{"name": "Simin", "email": "simin@harri.example"}],
                "matched_threads": ["AT-harri"],
                "matched_contacts": [],
                "active_thread_company_hits": ["Harri"],
                "snippet": "Following up.",
                "category": "active",
                "response_window_business_days": [3, 5],
                "business_days_since_sent": 7,
                "expected_response_by": "2026-05-18",
                "response_status": "response_overdue",
                "recommended_action": "follow_up",
                "confidence": "high",
            },
            {  # (2) awaiting, recent, warm baseline match
                "thread_id": "T-B",
                "subject": "Catching up",
                "sent_at": in_window_sent_at,
                "account_id": "primary",
                "account_label": "Primary",
                "to": [{"name": "Jane Doe", "email": "jane@example.org"}],
                "matched_threads": [],
                "matched_contacts": [{
                    "id": "p-jane", "name": "Jane Doe", "email": "jane@example.org",
                    "signal_class": "rc", "rc_tier": "outer",
                }],
                "active_thread_company_hits": [],
                "snippet": "Would love to grab time.",
                "category": "warm",
                "response_window_business_days": [5, 7],
                "business_days_since_sent": 0,
                "expected_response_by": "2026-05-29",
                "response_status": "awaiting_response",
                "recommended_action": "monitor",
                "confidence": "medium",
            },
            {  # (3) awaiting, OUT-of-window: must be suppressed
                "thread_id": "T-C",
                "subject": "Earlier ping",
                "sent_at": out_of_window_sent_at,
                "account_id": "primary",
                "account_label": "Primary",
                "to": [{"name": "Alex", "email": "alex@example.org"}],
                "matched_threads": [],
                "matched_contacts": [],
                "active_thread_company_hits": [],
                "snippet": "...",
                "category": "unknown",
                "response_window_business_days": [7, 10],
                "business_days_since_sent": 3,
                "expected_response_by": "2026-05-30",
                "response_status": "awaiting_response",
                "recommended_action": "monitor",
                "confidence": "low",
            },
            {  # (4) unknown status / missing timestamp: must be suppressed
                "thread_id": "T-D",
                "subject": "(no subject)",
                "sent_at": None,
                "account_id": "primary",
                "account_label": "Primary",
                "to": [],
                "matched_threads": [],
                "matched_contacts": [],
                "active_thread_company_hits": [],
                "snippet": "",
                "category": "unknown",
                "response_window_business_days": [7, 10],
                "business_days_since_sent": None,
                "expected_response_by": None,
                "response_status": "unknown",
                "recommended_action": "ask_todd",
                "confidence": "low",
            },
        ],
    }

    orig_email_overlay = core.email_overlay
    try:
        core.email_overlay = lambda baseline=None, threads=None: fake_overlay  # type: ignore
        signals, source_state, _recons = _email_signals(
            baseline=[], threads=[],
            window_start=window_start, window_end=window_end, now=now,
        )
    finally:
        core.email_overlay = orig_email_overlay  # type: ignore

    by_thread = {}
    for s in signals:
        for ev in s.get("evidence") or []:
            tid = ev.get("thread_id")
            if tid:
                by_thread.setdefault(tid, []).append(s)

    ck("T-A" in by_thread, "overdue + in-thread sent followup surfaces as a signal")
    ck("T-B" in by_thread, "in-window warm awaiting sent followup surfaces")
    ck("T-C" not in by_thread, "out-of-window awaiting sent followup is suppressed")
    ck("T-D" not in by_thread, "unknown-status sent followup is suppressed")

    sa = by_thread.get("T-A", [])[:1]
    if sa:
        s = sa[0]
        ck(s["signal_type"] == "response_overdue",
           f"T-A signal_type=response_overdue (got {s['signal_type']})")
        ck(s["source"] == "email", "T-A source=email")
        ck(s["grounding"] == "system_detected", "T-A grounding=system_detected")
        ck(s["strategic_relevance"] == "high", "T-A strategic_relevance=high")
        ck(s["active_thread_ids"] == ["AT-harri"],
           f"T-A active_thread_ids=[AT-harri] (got {s['active_thread_ids']})")
        ck(s["confidence"] == 0.85, f"T-A confidence=0.85 (got {s['confidence']})")
        ck("follow up" in (s["recommended_action"] or "").lower(),
           "T-A recommended_action mentions 'follow up'")
        ev = (s["evidence"] or [{}])[0]
        ck(ev.get("source") == "email_overlay.sent_followups",
           "T-A evidence sourced from email_overlay.sent_followups")

    sb = by_thread.get("T-B", [])[:1]
    if sb:
        s = sb[0]
        ck(s["signal_type"] == "awaiting_response",
           f"T-B signal_type=awaiting_response (got {s['signal_type']})")
        ck(s["contact_id"] == "p-jane",
           f"T-B contact_id=p-jane (got {s['contact_id']})")
        ck(s["strategic_relevance"] == "medium",
           f"T-B strategic_relevance=medium (got {s['strategic_relevance']})")
        ck(s["confidence"] == 0.70, f"T-B confidence=0.70 (got {s['confidence']})")
        ck("monitor" in (s["recommended_action"] or "").lower(),
           "T-B recommended_action mentions 'monitor'")

    print(f"--- relationship_signals sent_followups smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO anchor date (default: today).")
    p.add_argument("--hours", type=int, default=24, help="Look-back window in hours.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--cache", action="store_true",
                   help="Write the report to system/.cache/relationship_signals.json.")
    p.add_argument("--smoke", action="store_true",
                   help="Run the sent_followups signal-emission smoke (no inbox I/O).")
    args = p.parse_args()
    if args.smoke:
        return _smoke_sent_followups()
    today = date.fromisoformat(args.date) if args.date else date.today()
    report = build_report(today=today, hours=args.hours)
    seen_fingerprints = report.pop("_seen_fingerprints", {})
    if args.cache:
        _save_seen_fingerprints(seen_fingerprints, today)
        core.write_cache("relationship_signals", report, source="relationship_signals.py")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
