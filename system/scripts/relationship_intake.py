#!/usr/bin/env python3
"""
relationship_intake.py — Executive relationship intelligence intake and mutation engine.

Detects relationship-bearing interaction threads, classifies interaction signal
types, assesses trust trajectory, and emits review-first mutation proposals
against the contact graph (baseline_index.json) and interaction ledger.

Signal path:
  thread → entity resolution → signal classification → trust assessment →
  mutation proposals → pending persistence → confirmation → durable graph update

Invariants:
  - Default (apply=False): all mutations require_confirmation=True. No auto-mutations.
  - apply=True only ever auto-applies for an already-known contact (never
    creates a new baseline entry from extracted text) -- confirms the
    interaction AND touches last_touch in baseline_index.json via
    mutations.touch_contact(). See baseline_touch_status in the result.
  - apply=True never auto-adds a new contact -- a genuinely new contact's
    baseline record is created only through the separate, explicit review
    step: record_interaction(confirmed=True) / POST /ingest/relationship/
    confirm. RB-2026-08-28: this used to do nothing for a new contact even
    on confirm (only interaction_ledger.json's claim_status changed,
    baseline_index.json was never touched through any path) -- the
    contact_upsert proposal built in process_relationship_thread() was
    real but nothing ever applied it. Fixed: record_interaction() now
    creates the baseline entry when confirming an interaction for a
    contact_id not already in baseline. See baseline_contact_status in the
    result. No auto-tier promotion regardless of apply.
  - persistence_status is always explicit — never silent.
  - process_relationship_thread() is idempotent on the same interaction_id.
"""
from __future__ import annotations

import difflib
import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import personal_relationship_guard as prg

SYSTEM_DIR = Path(__file__).resolve().parent.parent
INTERACTION_LEDGER_PATH = SYSTEM_DIR / "interaction_ledger.json"
BASELINE_INDEX_PATH = SYSTEM_DIR / "baseline_index.json"

# ---------------------------------------------------------------------------
# Signal taxonomy
# ---------------------------------------------------------------------------

SIGNAL_TYPES = frozenset({
    "unsolicited_positive_followup",
    "meeting_completed",
    "warm_outreach_responded",
    "cold_outreach",
    "content_engagement",
    "referral_sent",
    "referral_received",
    "event_encounter",
    "introduction_made",
    "direct_inquiry",
    "unknown",
    "thought_leader_alignment",
})

TRUST_DELTA: dict[str, int] = {
    "referral_sent": 3,
    "unsolicited_positive_followup": 2,
    "direct_inquiry": 2,
    "referral_received": 2,
    "meeting_completed": 1,
    "warm_outreach_responded": 1,
    "content_engagement": 1,
    "event_encounter": 1,
    "introduction_made": 1,
    "cold_outreach": 0,
    "unknown": 0,
    "thought_leader_alignment": 1,
}

RELATIONSHIP_STATES = frozenset({
    "cold",
    "warming",
    "warm",
    "active",
    "strategic",
    "dormant",
})

PERSISTENCE_STATUSES = frozenset({
    "RB recorded",
    "RB updated",
    "RB proposed",
    "RB blocked",
    "RB skipped",
    "RB did not persist",
    "pending confirmation",
})

SOURCE_TYPES = frozenset({
    "email",
    "linkedin_message",
    "linkedin_post",
    "phone_call",
    "in_person",
    "event",
    "text_message",
    "social_dm",
    "conversation",
    # RB-DEFECT-2026-09-18 (Sal Nazir call-note incident): the API's own
    # RelationshipIntakeBody.source_type field docstring (system/api/
    # server.py) has always documented a DIFFERENT, wider vocabulary --
    # "email | linkedin_message | linkedin_post | transcript | sms |
    # call_note | meeting_note | recruiter_email" -- that barely overlapped
    # this enforcement set. A caller following the documented contract and
    # passing source_type="call_note" silently fell through the `not in
    # SOURCE_TYPES` branch below and got hard-reassigned to "email", with no
    # error or warning -- confirmed live: a phone call recap was persisted
    # and reported as an email. Reconciled by recognizing every documented
    # value here instead of silently discarding the caller's actual channel.
    "transcript",
    "sms",
    "call_note",
    "meeting_note",
    "recruiter_email",
})

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------

_EXEC_TITLES = (
    "President", "CEO", "COO", "CFO", "CTO", "CMO", "CRO",
    "Founder", "Co-Founder", "Managing Director", "Managing Partner",
    "Executive Director", "Principal", "Partner", "Vice President",
    "SVP", "EVP", "VP",
)
_EXEC_TITLE_PATTERN = "|".join(re.escape(t) for t in _EXEC_TITLES)

_POSITIVE_TONE_KEYWORDS = {
    "great", "wonderful", "pleasure", "enjoyed", "impressive", "excellent",
    "valuable", "appreciate", "thank", "outstanding", "remarkable", "inspiring",
    "insightful", "love", "excited", "look forward", "fantastic",
    "honored", "privilege", "impressed",
}

_SIGNAL_INDICATORS: dict[str, list[str]] = {
    "unsolicited_positive_followup": [
        "following up", "follow up", "wanted to reach out", "wanted to say",
        "taking a moment", "reaching out", "just wanted", "following our",
        "after our", "since our", "after meeting", "after speaking",
    ],
    "meeting_completed": [
        "great meeting", "great call", "great conversation",
        "coffee chat", "after our meeting", "after our call",
        "we spoke", "we talked", "we met",
        # First-person spoken narration — voice-memo/dictation captures report a
        # completed conversation in "I spoke with X" form, not "we spoke" or
        # written-correspondence phrasing. Without these, a dictated recap of a
        # real conversation classifies as signal_type="unknown" (trust_delta=0),
        # which understates a relationship touchpoint that actually happened.
        "i spoke with", "i talked with", "i talked to", "i met with",
        "i caught up with", "caught up with",
        # RB-DEFECT-2026-09-18: a third-person call recap ("Phone call with
        # X on <date>... During the call X said...") -- the shape a call-note
        # dictation naturally takes when narrating what the OTHER person
        # said, not what "I" did -- matched none of the above and fell
        # through to signal_type="unknown" -> trust_delta=0 -> relationship_
        # state="cold" -> strategic_classification="Network Contact", for a
        # real completed phone call carrying substantive human-source
        # intelligence. Confirmed live 2026-09-18 (Sal Nazir/PAR Technology
        # call). "call with" alone is intentionally NOT listed (too broad --
        # would match "email call with" style fragments); these anchor on
        # the call actually being described as an event that happened.
        "phone call with", "during the call", "on a call with", "on the call",
        "spoke by phone",
    ],
    "direct_inquiry": [
        "would love to", "interested in working", "would be great to",
        "open to", "would you be open", "are you available",
        "could we explore", "can we connect", "would you like to",
    ],
    "referral_sent": [
        "referred you to", "introduced you to", "mentioned you to",
        "gave your name", "shared your contact", "passed your info",
    ],
    "referral_received": [
        "was referred to you", "someone suggested", "was told to reach out",
        "heard great things about you", "recommended you",
    ],
    "content_engagement": [
        "saw your post", "saw your article", "read your piece",
        "liked your post", "commented on your", "shared your",
    ],
    "warm_outreach_responded": [
        "thanks for reaching out", "appreciate you reaching out",
        "glad you reached out", "happy to connect",
    ],
}

_ECOSYSTEM_INDICATORS: dict[str, list[str]] = {
    "hospitality_table": ["hospitality table", "hospitality-table"],
    "restaurant_tech": ["restaurant", "pos system", "toast ", "operator", "hospitality tech"],
    "hr_consulting": [
        "human resource", "hr consulting", "bridgepoint",
        "organizational consulting", "human resource consortium",
    ],
    "executive_network": [
        "executive", "president", "ceo", "coo", "c-suite", "chief",
        "board", "managing director",
    ],
}

# Explicit commitments/requests buried in a transcript ("we should help him")
# carry a specific to-do that the trust-tier posture ladder isn't built to
# express — a "cold" contact with an embedded ask still needs the ask acted
# on, not "Monitor — no immediate action required". Detected independently of
# signal_type/trust_delta so it can't be silently outranked by them.
_ACTION_ASK_INDICATORS = [
    "if there's any way we can help", "if there is any way we can help",
    "any way we can help", "we should help", "we should do that",
    "let's help", "lets help", "can we help him", "can we help her",
    "would be great if we could help", "help him with", "help her with",
    "help him for", "help her for", "we need to help", "we should reach out",
    "make sure we follow up", "make sure to follow up",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"ri-{ts}-{uuid.uuid4().hex[:6]}"


def _contact_id_from_name(name: str) -> str:
    """'Elizabeth Jenswold' → 'elizabeth-jenswold'."""
    slug = re.sub(r"[^a-z0-9\s]", "", name.lower())
    return re.sub(r"\s+", "-", slug.strip())


_FUZZY_MATCH_THRESHOLD = 0.82


def _load_baseline_contacts() -> list[dict]:
    try:
        data = json.loads(BASELINE_INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _resolve_existing_contact_id(name: str, contact_id: str) -> tuple[str, str | None]:
    """Return (resolved_contact_id, matched_existing_name_or_None).

    A voice-memo transcript can misspell a name close enough that the naive
    slug (_contact_id_from_name) creates a brand-new orphan contact instead
    of resolving to the real baseline entry — e.g. a whisper transcription
    of "Jeff Wayman" as "Jeff Waman" created contact_id "jeff-waman" while
    the existing baseline contact (with an open loop already attached) is
    "jeff-wayman". That leaves the two records — and their evidence — split
    with no way to know they're the same person.

    Exact contact_id match wins immediately. Otherwise, fuzzy-match against
    baseline contacts sharing the same first token (first name) only, so
    "Jeff Waman" can't accidentally resolve to an unrelated "Jeff Chen" —
    the first-name gate plus a high similarity threshold (0.82) keeps this
    conservative: it catches transcription-distance-1 misspellings, not
    genuinely different people who happen to share a first name.

    RB-2026-08-28: confirmed live -- the exact-contact_id-match branch used
    to return matched_existing_name=None unconditionally, on the assumption
    that an id match means the incoming name is already correct. False for
    a contact whose canonical `name` was corrected in place after the id
    was first slugged from an earlier misspelling (ids are immutable slugs;
    correcting a display name later never renames one) -- "Jeff Caplan"
    slugs to the existing id "jeff-caplan" even though that contact's
    on-file name was already corrected to "Jeff Caplin". Now returns the
    on-file name whenever it differs from the incoming name, on either
    match path.
    """
    contacts = _load_baseline_contacts()
    by_id = {c.get("id"): c for c in contacts}
    if contact_id in by_id:
        on_file_name = by_id[contact_id].get("name")
        matched = on_file_name if on_file_name and on_file_name.strip().lower() != name.strip().lower() else None
        return contact_id, matched

    name_lower = name.lower().strip()
    tokens = name_lower.split()
    if not tokens:
        return contact_id, None
    first_token = tokens[0]

    best_ratio = 0.0
    best_contact: dict | None = None
    for c in contacts:
        cname = (c.get("name") or "").lower().strip()
        if not cname or not cname.split() or cname.split()[0] != first_token:
            continue
        ratio = difflib.SequenceMatcher(None, name_lower, cname).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_contact = c

    if best_contact and best_ratio >= _FUZZY_MATCH_THRESHOLD:
        return best_contact["id"], best_contact.get("name")
    return contact_id, None


def _detect_action_ask(text: str) -> bool:
    """Return True if the text contains an explicit request/commitment to act."""
    text_lower = text.lower()
    return any(ind in text_lower for ind in _ACTION_ASK_INDICATORS)


def _classify_signal_type(text: str) -> str:
    text_lower = text.lower()
    has_positive = any(kw in text_lower for kw in _POSITIVE_TONE_KEYWORDS)

    for signal_type, indicators in _SIGNAL_INDICATORS.items():
        if any(ind in text_lower for ind in indicators):
            if signal_type == "unsolicited_positive_followup" and not has_positive:
                continue
            return signal_type

    return "unknown"


def _detect_ecosystem_tags(text: str, explicit_tags: list[str] | None = None) -> list[str]:
    text_lower = text.lower()
    tags = list(explicit_tags or [])
    for tag, keywords in _ECOSYSTEM_INDICATORS.items():
        if any(kw in text_lower for kw in keywords) and tag not in tags:
            tags.append(tag)
    return tags


def _classify_exec_weight(role: str | None) -> int:
    if not role:
        return 0
    rl = role.lower()
    if any(kw in rl for kw in {"president", "ceo", "coo", "cfo", "cto", "founder", "co-founder"}):
        return 2
    if any(kw in rl for kw in {"vp", "vice president", "svp", "evp", "managing director", "managing partner", "principal"}):
        return 1
    return 0


def _derive_relationship_state(trust_delta: int, signal_type: str) -> str:
    if signal_type in {"referral_sent", "referral_received", "direct_inquiry"} and trust_delta >= 2:
        return "strategic"
    if trust_delta >= 2:
        return "warm"
    if trust_delta >= 1:
        return "warming"
    return "cold"


def _classify_strategic_role(
    exec_weight: int,
    ecosystem_tags: list[str],
    trust_delta: int,
    signal_type: str | None = None,
) -> str:
    if signal_type == "thought_leader_alignment" and len(ecosystem_tags) >= 2:
        return "Strategic Thought Leader"
    if exec_weight >= 2 and len(ecosystem_tags) >= 2:
        return "Strategic Ecosystem Connector"
    if exec_weight >= 1 and trust_delta >= 2:
        return "Executive Strategic Contact"
    if trust_delta >= 2:
        return "Warm Emerging Contact"
    if trust_delta >= 1:
        return "Active Relationship Contact"
    return "Network Contact"


def _derive_recommended_posture(strategic_classification: str, relationship_state: str) -> str:
    if "Strategic Thought Leader" in strategic_classification:
        return "Monitor content for thesis alignment — evaluate for amplification and collaborative opportunity"
    if "Strategic Ecosystem Connector" in strategic_classification:
        return "Low-pressure trust cultivation — long-horizon ecosystem relationship management"
    if "Executive Strategic" in strategic_classification:
        return "Consistent, value-first engagement — monitor for strategic opportunity window"
    if relationship_state in {"warm", "strategic"}:
        return "Maintain warmth — schedule light-touch follow-up within 30 days"
    if relationship_state == "warming":
        return "Engage deliberately — one thoughtful touchpoint in the next 60 days"
    return "Monitor — no immediate action required"


def _extract_entity_from_text(text: str) -> tuple[str | None, str | None, str | None]:
    """Attempt basic entity extraction. Returns (name, org, role). Best-effort."""
    name: str | None = None
    org: str | None = None
    role: str | None = None

    # "Name, Title[, Org]" — signature block pattern
    sig_match = re.search(
        rf"([A-Z][a-z]+ (?:[A-Z][a-z]+ )*[A-Z][a-zA-Z'-]+),\s*({_EXEC_TITLE_PATTERN})",
        text,
    )
    if sig_match:
        name = sig_match.group(1).strip()
        role = sig_match.group(2).strip()
        after = text[sig_match.end() : sig_match.end() + 100]
        org_match = re.match(r",\s*([A-Z][A-Za-z\s&/]+?)(?:\s*[,\n]|$)", after)
        if org_match:
            org = org_match.group(1).strip()

    if not name:
        # "From: Name <email>" or "From: Name"
        from_match = re.search(r"From:\s*([A-Z][a-z]+ [A-Z][a-zA-Z'-]+)", text)
        if from_match:
            name = from_match.group(1)

    if not role:
        # "Title at/of Org"
        role_match = re.search(
            rf"({_EXEC_TITLE_PATTERN})\s+(?:at|of)\s+([A-Z][A-Za-z\s&]+?)(?:\s*[,\n/]|$)",
            text,
        )
        if role_match:
            role = role_match.group(1).strip()
            org = role_match.group(2).strip()

    return name, org, role


# ---------------------------------------------------------------------------
# Ledger persistence
# ---------------------------------------------------------------------------


def _load_ledger(store_path: Path | None = None) -> dict:
    path = store_path or INTERACTION_LEDGER_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"_schema_version": "1.0", "interactions": []}


def _save_ledger(ledger: dict, store_path: Path | None = None) -> None:
    path = store_path or INTERACTION_LEDGER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, default=str))


def _write_pending_interaction(interaction: dict, store_path: Path | None = None) -> None:
    ledger = _load_ledger(store_path=store_path)
    existing_ids = {i.get("id") for i in ledger.get("interactions", [])}
    if interaction["id"] not in existing_ids:
        ledger["interactions"].append(interaction)
    ledger["_last_updated"] = _timestamp()
    _save_ledger(ledger, store_path=store_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_relationship_thread(
    text: str,
    entity_name: str | None = None,
    entity_org: str | None = None,
    entity_role: str | None = None,
    interaction_date: str | None = None,
    source_type: str = "email",
    ecosystem_tags: list[str] | None = None,
    signal_type_override: str | None = None,
    apply: bool = False,
    store_path: Path | None = None,
    business_override: bool = False,
) -> dict:
    """Extract relationship intelligence from an interaction thread.

    Entity resolution order: explicit params → text extraction.
    At least entity_name must be resolvable to emit mutations.

    RB-DEFECT-2026-08-21: `apply` was accepted by the API request schema but
    silently dropped before it ever reached this function -- the API
    documented a one-step "settled fact" mode that did not exist. Now: when
    apply=True AND the entity resolved to an existing baseline contact
    (exact contact_id match or a high-confidence fuzzy name match, not a
    brand-new never-seen entity), the interaction is auto-confirmed in the
    same call, matching
    the auto_promote intent in CANONICAL_REGISTRY.yaml's
    mutation_authorization policy. A brand-new/unresolved entity always
    stays proposed regardless of apply -- creating a new contact record from
    freeform extracted text without review is exactly the kind of
    ambiguous-referent case that policy carves out, mirroring the
    never-merge-on-name-alone rule in identity_match_review.py.

    Returns canonical CoS mutation surface:
      interactions       — list of classified interaction event records
      mutation_proposals — review-first contact + ledger mutations
      cos_surface        — structured CoS output block
      persistence_status — always explicit
    """
    if not text or not text.strip():
        return {
            "interactions": [],
            "mutation_proposals": [],
            "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": "Empty input. No relationship intelligence extracted.",
        }

    if source_type not in SOURCE_TYPES:
        source_type = "email"

    # Entity resolution
    extracted_name, extracted_org, extracted_role = _extract_entity_from_text(text)
    name = entity_name or extracted_name
    org = entity_org or extracted_org
    role = entity_role or extracted_role

    # RB-2026-08-28: personal vs. business gate, per Todd's explicit direction
    # -- RB is a business/professional relationship-capital tool; personal
    # relationships and personal content must never be recorded or reported.
    # Checked here, before any interaction/mutation is built, same
    # "exclude at the boundary" discipline as the declined-calendar-invite
    # exclusion in rb_core.calendar_overlay(). A sender match is overridable
    # via business_override (the user explicitly said THIS item is
    # business); a topic match never is -- personal content doesn't become
    # business because of who sent it.
    guard = prg.classify(
        sender_name=name or "", text=text, explicit_business_flag=business_override,
    )
    if guard.is_personal:
        return {
            "interactions": [],
            "mutation_proposals": [],
            "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": (
                f"Classified as personal content ({guard.reason}) -- RB does not "
                "record or report personal relationships. If this is actually "
                "business, re-call with business_override=true."
            ),
        }

    if not name:
        return {
            "interactions": [],
            "mutation_proposals": [],
            "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": (
                "No named entity detected. "
                "Provide entity_name to enable relationship intelligence mutation."
            ),
        }

    # RB-2026-09-03: confirmed live -- Todd's own LinkedIn observation posts
    # (thought_leader_alignment signals about his own commentary, e.g. "Todd
    # observation: Burger King buying stores...") were being extracted with
    # Todd himself as the resolved entity, producing a real interaction_
    # ledger proposal that surfaced in the Daily Brief's Pending
    # Confirmations as "confirm my interaction with Todd Vahlsing" -- you
    # cannot have a business interaction with yourself as the counterparty.
    # Same "exclude at the boundary" shape as the personal-content guard
    # just above. This does not fix the upstream caller that's routing
    # Todd's own posts through the relationship-intake pipeline at all
    # (a real, separate gap) -- it stops the nonsensical proposal from
    # reaching the ledger regardless of which caller does that.
    # Bare "todd" is deliberately excluded -- a real contact can legitimately
    # be a different Todd; "todd vahlsing"/"vahlsing" are distinctive enough
    # not to collide with a real business contact's name.
    _self_names = {"todd vahlsing", "vahlsing"}
    if (name or "").strip().lower() in _self_names:
        return {
            "interactions": [],
            "mutation_proposals": [],
            "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": (
                "Resolved entity is Todd himself, not a business contact -- RB does not "
                "record an 'interaction' with its own user. If this was meant to update "
                "intelligence about a third party the content mentions, re-call with the "
                "correct entity_name."
            ),
        }

    # Classification
    signal_type = (
        signal_type_override
        if signal_type_override and signal_type_override in SIGNAL_TYPES
        else _classify_signal_type(text)
    )
    trust_delta = TRUST_DELTA.get(signal_type, 0)
    relationship_state = _derive_relationship_state(trust_delta, signal_type)
    exec_weight = _classify_exec_weight(role)
    detected_tags = _detect_ecosystem_tags(text, ecosystem_tags)
    strategic_classification = _classify_strategic_role(exec_weight, detected_tags, trust_delta, signal_type)
    recommended_posture = _derive_recommended_posture(strategic_classification, relationship_state)
    contact_id, matched_existing_name = _resolve_existing_contact_id(name, _contact_id_from_name(name))
    # matched_existing_name is only set on the fuzzy-match path (see that
    # function's docstring) -- an exact contact_id hit returns None there
    # too, so "is this actually a known contact" (exact OR fuzzy) needs its
    # own check for the apply gate below.
    is_known_contact = contact_id in {c.get("id") for c in _load_baseline_contacts()}

    # An explicit ask overrides the trust-tier posture ladder — a specific
    # commitment ("we should help him with intros") must not be buried under
    # a generic "Monitor" recommendation just because the contact is cold.
    action_ask_detected = _detect_action_ask(text)
    if action_ask_detected:
        recommended_posture = (
            f"ACT — source text contains a specific request or commitment: review the "
            f"full transcript and follow up directly. (Underlying tier guidance: "
            f"{recommended_posture})"
        )

    # Who Matters Now score for this interaction (+3 recency bonus = same-day)
    wmn_score = trust_delta + exec_weight + 3
    if action_ask_detected:
        wmn_score += 5

    now = _timestamp()
    interaction_id = _make_id()
    today_str = interaction_date or date.today().isoformat()

    # RB-2026-08-28: confirmed live -- contact_data["name"] below already
    # preferred matched_existing_name over the raw extracted name (with its
    # own comment explaining why: a mis-transcribed/misspelled variant must
    # not overwrite the real baseline name on confirm). This interaction
    # record and the summaries derived from it did not follow the same
    # rule, so a fuzzy-matched interaction against "Jeff Caplin" (baseline,
    # already corrected once before) still displayed as "Jeff Caplan" (the
    # incoming spelling) everywhere except the eventual contact_upsert --
    # exactly the kind of re-surfacing of an already-fixed error a human
    # has to catch by hand instead of the system getting it right natively.
    display_name = matched_existing_name or name
    interaction_event = {
        "id": interaction_id,
        "contact_id": contact_id,
        "entity": {"name": display_name, "org": org, "role": role},
        "interaction_date": today_str,
        "source_type": source_type,
        "signal_type": signal_type,
        "trust_delta": trust_delta,
        "relationship_state_proposed": relationship_state,
        "strategic_classification": strategic_classification,
        "recommended_posture": recommended_posture,
        "executive_weight": exec_weight,
        "ecosystem_tags": detected_tags,
        "who_matters_now_score": wmn_score,
        "action_ask_detected": action_ask_detected,
        "entity_resolution_note": (
            f"Extracted as '{name}'; resolved to existing baseline contact "
            f"'{matched_existing_name}' (contact_id={contact_id})."
        ) if matched_existing_name else None,
        "source_text_snippet": text[:300],
        "created_at": now,
        "claim_status": "proposed",
        "persistence_status": "pending confirmation",
        "confirmed_at": None,
    }

    circles = [t for t in detected_tags if "hospitality" in t]
    mutation_proposals = [
        {
            "mutation_type": "contact_upsert",
            "target": "system/baseline_index.json",
            "operation": "add_if_absent_else_touch",
            "contact_id": contact_id,
            "contact_data": {
                "id": contact_id,
                # Use the canonical baseline name when fuzzy-resolved, not the
                # raw (possibly mis-transcribed) extracted name — otherwise
                # confirming this proposal would overwrite "Jeff Wayman" with
                # the whisper-transcription variant "Jeff Waman".
                "name": matched_existing_name or name,
                "current_company": org,
                "current_role": role,
                "signal_class": "LKI",
                "rc_state": None,
                "rc_tier": None,
                "last_touch": today_str,
                "circles": circles,
                "tags": detected_tags,
                "sources": [f"relationship_intake_{today_str}"],
                "notes": (
                    f"[{today_str}] {signal_type.replace('_', ' ').title()} — "
                    f"{strategic_classification} — {relationship_state} state."
                ),
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /ingest/relationship/confirm",
        },
        {
            "mutation_type": "interaction_event",
            "target": "system/interaction_ledger.json",
            "operation": "append",
            "interaction_id": interaction_id,
            "event_summary": {
                "contact": display_name,
                "org": org,
                "role": role,
                "date": today_str,
                "signal_type": signal_type,
                "trust_delta": trust_delta,
                "relationship_state": relationship_state,
                "ecosystem_tags": detected_tags,
            },
            "requires_confirmation": True,
            "persistence_endpoint": "POST /ingest/relationship/confirm",
        },
    ]

    _write_pending_interaction(interaction_event, store_path=store_path)

    auto_confirmed = False
    baseline_touch_status = None
    baseline_tags_status = None
    if apply and is_known_contact:
        confirmed_record = record_interaction(interaction_id, confirmed=True, store_path=store_path)
        if "error" not in confirmed_record:
            interaction_event = confirmed_record
            auto_confirmed = True
            # RB-2026-08-24: confirming a known contact's interaction used to
            # update only the interaction ledger, never baseline_index.json's
            # last_touch -- so staleness/decay tracking never reflected an
            # apply:true confirmation. contact_upsert's "touch" half (not the
            # add-new-contact half, which stays review-first on purpose) is
            # exactly what touch_contact() already does safely, so reuse it
            # rather than writing a second, parallel baseline writer.
            try:
                import mutations  # local import -- keep this module importable standalone
                mutations.touch_contact(contact_id, today_str, source=f"relationship_intake_{today_str}")
                baseline_touch_status = "applied"
            except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the confirmed interaction
                baseline_touch_status = f"failed: {exc}"

            # RB-2026-08-24: apply the tags half of the contact_upsert
            # proposal too -- additive-only (mutations.cmd_contact_update's
            # tags_add never removes or overwrites existing tags), so it
            # carries none of the risk a company/role overwrite would.
            # current_company/current_role deliberately stay review-first,
            # matching every other ingestion path in this codebase
            # (linkedin_ingest.py, contacts_ingest.py, hubspot_ingest.py all
            # explicitly never auto-overwrite an operator-confirmed company
            # or role from a single free-text mention -- a casual "when he
            # was at McKinsey" reference is exactly the kind of extraction
            # that could silently corrupt a correct existing record).
            if detected_tags:
                try:
                    ns = type("Ns", (), dict(
                        id=contact_id, company=None, role=None, email=None, phone=None,
                        linkedin=None, last_touch=None, signal_class=None, rc_tier=None,
                        rc_state=None, relationship_domain=None, notes=None,
                        notes_replace=False, tags_add=detected_tags, dry_run=False,
                    ))()
                    rc = mutations.cmd_contact_update(ns)
                    baseline_tags_status = "applied" if rc == 0 else "failed: cmd_contact_update returned nonzero"
                except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the confirmed interaction
                    baseline_tags_status = f"failed: {exc}"

    cos_surface = {
        "entity": {"name": display_name, "org": org, "role": role, "contact_id": contact_id},
        "strategic_classification": strategic_classification,
        "signals": {
            "signal_type": signal_type,
            "trust_delta": trust_delta,
            "ecosystem_tags": detected_tags,
        },
        "relationship_state_proposed": relationship_state,
        "recommended_posture": recommended_posture,
        "who_matters_now_score": wmn_score,
        "interaction_id": interaction_id,
        "interaction_date": today_str,
    }

    result: dict = {
        "interactions": [interaction_event],
        "mutation_proposals": mutation_proposals,
        "cos_surface": cos_surface,
        "persistence_status": "RB recorded" if auto_confirmed else "pending confirmation",
        "interaction_count": 1,
        "mutation_proposal_count": len(mutation_proposals),
        "auto_confirmed": auto_confirmed,
        "baseline_touch_status": baseline_touch_status,
        "baseline_tags_status": baseline_tags_status,
    }
    if apply and not auto_confirmed:
        result["apply_note"] = (
            "apply=true requested, but the entity did not resolve to an existing "
            "baseline contact with confidence -- creating a new contact from "
            "extracted text needs review first. Held as pending confirmation."
            if not is_known_contact else
            "apply=true requested but confirmation failed; left as pending confirmation."
        )

    # RB 9.33 / DEFECT-016 — Networking Introduction Engine.
    # If the input reads as a networking event (roundtable, Hospitality Table, SCN,
    # virtual coffee, conference, referral conversation, etc.) automatically engage
    # the networking lens and append introduction candidates to the response.
    # Best-effort: a failure here must NOT suppress the baseline intake result.
    try:
        import networking_lens as _nl  # local import — same package directory
        _detected, _event_type = _nl.detect_networking_event(text)
        if _detected:
            _scan = _nl.scan_networking_event(text)
            result["networking_scan"] = _scan
    except Exception:  # noqa: BLE001
        pass  # networking lens is enhancement-only; never block intake

    return result


def record_interaction(
    interaction_id: str,
    confirmed: bool = True,
    store_path: Path | None = None,
) -> dict:
    """Confirm or reject a pending interaction event.

    Returns the updated interaction record, or an error dict if not found.

    RB-2026-08-28: confirming an interaction for a contact NOT already in
    baseline_index.json used to update only this ledger -- the real
    contact_upsert proposal process_relationship_thread() built was never
    applied by any path, including this one (the explicit, human-reviewed
    confirm step that review-first design defers new-contact creation to
    in the first place). Fixed: creates the baseline entry here, using
    fields already on the interaction record, when contact_id isn't
    already known. See baseline_contact_status in the returned record.
    """
    ledger = _load_ledger(store_path=store_path)
    for idx, entry in enumerate(ledger.get("interactions", [])):
        if entry.get("id") == interaction_id:
            if confirmed:
                ledger["interactions"][idx]["claim_status"] = "confirmed"
                ledger["interactions"][idx]["persistence_status"] = "RB recorded"
                ledger["interactions"][idx]["confirmed_at"] = _timestamp()

                contact_id = entry.get("contact_id")
                baseline_contact_status = None
                if contact_id and contact_id not in {c.get("id") for c in _load_baseline_contacts()}:
                    try:
                        import mutations  # local import -- keep this module importable standalone
                        entity = entry.get("entity") or {}
                        ns = type("Ns", (), dict(
                            id=contact_id,
                            name=entity.get("name") or contact_id,
                            company=entity.get("org"),
                            role=entity.get("role"),
                            linkedin=None, email=None, phone=None,
                            source=f"relationship_intake_confirm_{date.today().isoformat()}",
                            signal_class="LKI", rc_tier=None,
                            last_touch=entry.get("interaction_date"),
                            notes=f"Created from confirmed relationship-intake interaction {interaction_id}.",
                            dry_run=False,
                        ))()
                        rc = mutations.cmd_contact_add(ns)
                        baseline_contact_status = "created" if rc == 0 else "failed: cmd_contact_add returned nonzero"
                    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the confirmed interaction
                        baseline_contact_status = f"failed: {exc}"
                    ledger["interactions"][idx]["baseline_contact_status"] = baseline_contact_status
            else:
                ledger["interactions"][idx]["claim_status"] = "rejected"
                ledger["interactions"][idx]["persistence_status"] = "RB skipped"
            ledger["_last_updated"] = _timestamp()
            _save_ledger(ledger, store_path=store_path)
            return ledger["interactions"][idx]
    return {"error": f"Interaction {interaction_id} not found."}


def query_interactions(
    contact_id: str | None = None,
    claim_status: str | None = None,
    signal_type: str | None = None,
    store_path: Path | None = None,
) -> list[dict]:
    """Retrieve interaction events from the ledger by filter."""
    ledger = _load_ledger(store_path=store_path)
    results = list(ledger.get("interactions", []))
    if contact_id:
        results = [i for i in results if i.get("contact_id") == contact_id]
    if claim_status:
        results = [i for i in results if i.get("claim_status") == claim_status]
    if signal_type:
        results = [i for i in results if i.get("signal_type") == signal_type]
    return results


def query_who_matters_now(
    top_n: int = 10,
    min_score: int = 0,
    store_path: Path | None = None,
) -> list[dict]:
    """Return top N contacts ranked by Who Matters Now score.

    Score = sum of (trust_delta + executive_weight + recency_bonus) over
    all proposed and confirmed interactions in the ledger. Contacts are
    ranked descending. Only pending and confirmed interactions count;
    rejected entries are excluded.

    Recency bonus: ≤7 days → +3, ≤30 days → +2, ≤90 days → +1, older → 0.
    """
    ledger = _load_ledger(store_path=store_path)
    today = date.today()
    contact_scores: dict[str, dict] = {}

    for interaction in ledger.get("interactions", []):
        if interaction.get("claim_status") not in {"confirmed", "proposed"}:
            continue
        cid = interaction.get("contact_id")
        if not cid:
            continue

        idate_str = interaction.get("interaction_date", "")
        recency_bonus = 0
        if idate_str:
            try:
                idate = date.fromisoformat(idate_str)
                days_ago = (today - idate).days
                if days_ago <= 7:
                    recency_bonus = 3
                elif days_ago <= 30:
                    recency_bonus = 2
                elif days_ago <= 90:
                    recency_bonus = 1
            except ValueError:
                pass

        point = (
            interaction.get("trust_delta", 0)
            + interaction.get("executive_weight", 0)
            + recency_bonus
        )

        if cid not in contact_scores:
            contact_scores[cid] = {
                "contact_id": cid,
                "entity": interaction.get("entity", {}),
                "score": 0,
                "last_interaction": idate_str,
                "recent_signal": interaction.get("signal_type"),
                "strategic_classification": interaction.get("strategic_classification"),
                "relationship_state": interaction.get("relationship_state_proposed"),
                "ecosystem_tags": list(interaction.get("ecosystem_tags", [])),
                "interaction_count": 0,
            }

        contact_scores[cid]["score"] += point
        contact_scores[cid]["interaction_count"] += 1

        if idate_str >= (contact_scores[cid]["last_interaction"] or ""):
            contact_scores[cid]["last_interaction"] = idate_str
            contact_scores[cid]["recent_signal"] = interaction.get("signal_type")
            contact_scores[cid]["strategic_classification"] = interaction.get("strategic_classification")
            contact_scores[cid]["relationship_state"] = interaction.get("relationship_state_proposed")

        for tag in interaction.get("ecosystem_tags", []):
            if tag not in contact_scores[cid]["ecosystem_tags"]:
                contact_scores[cid]["ecosystem_tags"].append(tag)

    results = [v for v in contact_scores.values() if v["score"] >= min_score]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]
