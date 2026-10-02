#!/usr/bin/env python3
"""
intelligence_triage.py — Multi-type intelligence triage for RB.

DEFECT-015 fix: any user-supplied input (paste, upload, screenshot, transcript,
article) may contain multiple intelligence types that belong in different parts
of the RB architecture.  This module identifies ALL types present, extracts
each independently, and returns a triage result that the API and Custom GPT can
use to route each stream to its correct mutation endpoint.

Triage is ALWAYS read-only / review-first.  Every proposed mutation carries
requires_confirmation=True.  No writes happen here.

Entry point:
    triage_input(text, *, source_type, source_name, author_name,
                 author_company, event_at, captured_at) -> dict

Intelligence types:
    macro_signal          — market/industry/consumer/vendor signal
    micro_graph_build     — new entity topology dataset (first creation)
    micro_graph_enrichment — additional data for existing entity artifact
    ri_event              — person-level relationship signal
    strategic_memory      — user thesis / positioning / watchlist note
    noise                 — no actionable intelligence

Usage:
    python3 intelligence_triage.py --text "McDonald's Q2 comp sales ..."
    python3 intelligence_triage.py --file path/to/doc.txt
    python3 intelligence_triage.py --text "..." --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import opportunity_pipeline  # noqa: E402  # RB-DEFECT-037 — career pipeline triggers
import entity_alerts as ea  # noqa: E402  # RB-DEFECT-2026-08-29 — watchlist-entity mention detection

# ---------------------------------------------------------------------------
# Intelligence type constants
# ---------------------------------------------------------------------------

TYPE_MACRO = "macro_signal"
TYPE_MICRO_BUILD = "micro_graph_build"
TYPE_MICRO_ENRICH = "micro_graph_enrichment"
TYPE_RI = "ri_event"
TYPE_STRATEGIC = "strategic_memory"
TYPE_CAREER = "career_pipeline_update"
TYPE_EXEC_DECL = "executive_declaration"  # CEO first-person state change — authoritative, auto-mutates
TYPE_NOISE = "noise"

VALID_TYPES = frozenset({
    TYPE_MACRO, TYPE_MICRO_BUILD, TYPE_MICRO_ENRICH,
    TYPE_RI, TYPE_STRATEGIC, TYPE_CAREER, TYPE_EXEC_DECL, TYPE_NOISE,
})

# Mutation priority order — executive_declaration (CEO first-person statements) are the
# highest-fidelity source of truth and take absolute precedence. Career pipeline updates
# are second. Nothing from external intelligence overrides a direct CEO declaration.
MUTATION_ORDER = [TYPE_EXEC_DECL, TYPE_CAREER, TYPE_RI, TYPE_MICRO_ENRICH, TYPE_MICRO_BUILD,
                  TYPE_MACRO, TYPE_STRATEGIC, TYPE_NOISE]

# ---------------------------------------------------------------------------
# Executive declaration patterns (D3/D4 — RB-DEFECT-003/004)
# ---------------------------------------------------------------------------

# Maps regex pattern → (event_type, mutation_target)
_EXEC_DECL_PATTERNS: list[tuple[str, str, str]] = [
    # Opportunity acceptance
    (r"\b(i|i've|i have)\s+(accepted|signed|took|taking|accepted the)\b.*\b(offer|role|job|position)\b",
     "opportunity_accepted", "opportunity"),
    # Opportunity decline
    (r"\b(i|i've|i have|i'm)\s+(declined|passed|rejected|turned down|withdrawing|not moving forward)\b",
     "opportunity_declined", "opportunity"),
    # Relationship touch — outbound call/meeting/message
    (r"\b(i|i've)\s+(talked|spoke|spoken|met|met with|called|texted|emailed|messaged|reached out|connected)\b.{0,60}\b(today|this morning|this afternoon|earlier|just now|yesterday)\b",
     "relationship_touch", "relationship"),
    # Relationship touch — inbound received
    (r"\b(i\s+got|i\s+received|i\s+heard\s+from)\b.{0,60}\b(today|this morning|earlier)\b",
     "relationship_touch_inbound", "relationship"),
    # Loop/action completion
    (r"\b(i|i've|i have)\s+(sent|submitted|completed|finished|done|filed|signed|returned)\b",
     "action_completed", "loop"),
    # State resolved (third-person) — "the well pump is working", "it's fixed", "resolved".
    # Deliberately loose: precision for auto-closing the *right* loop is enforced downstream
    # by eolms.match_and_transition()'s own name/org/tag confidence gate, not by this regex.
    (r"\b(is|are|was|were|it's)\s+(working|fixed|resolved|installed|complete|completed|done|finished)\b",
     "state_resolved", "loop"),
    (r"\b(resolved|wrapped\s+up)\b",
     "state_resolved", "loop"),
    # Loop advanced — inbound signal moving a waiting loop forward, without requiring a
    # same-day marker like relationship_touch_inbound does ("Ryan responded", "heard back").
    (r"\b(responded|replied|confirmed|heard\s+back|got\s+back\s+to\s+me|moved\s+forward|moving\s+forward)\b",
     "loop_advanced", "loop"),
    # Personal/spiritual practice — Life Lens manual goals (RB-DEFECT-062), plus
    # personal-relationship events and life-timeline events (sibling gap — a
    # date night or a spouse's milestone had nowhere to route at all before).
    # Deliberately loose, same reasoning as state_resolved above: this is just
    # a cheap pre-filter to route to the right handler. The actual match
    # happens downstream in personal_log.record_personal_practice() /
    # record_personal_relationship_events() — a trigger here with no real
    # keyword match there safely resolves to "no_match", not a bad write.
    (r"\b(pray|prayed|praying|devotions?|bible|scripture|sunday service|worship service|"
     r"went to church|quality time with mary|mary and i|me and mary|mary and me|"
     r"date night|went on a date|had a date|retirement party|her graduation|"
     r"his graduation|her promotion|his promotion|surprise party|"
     r"supported (her|him|mary) at|anniversary)\b",
     "personal_practice_logged", "personal_log"),
    # Status update — start date / onboarding
    (r"\b(start\s+date|starting\s+(on|monday|next)|first\s+day|onboarding\s+(starts?|begins?|is))\b",
     "start_date_set", "opportunity"),
]

import re as _re_triage

def classify_executive_declaration(text: str) -> dict | None:
    """Return executive_declaration stream dict if text matches, else None.

    Confidence is always 'authoritative' — CEO first-person statements are the
    highest-fidelity source RB has and bypass confirmation requirements.
    """
    text_lower = text.strip().lower()
    # Only fire on short-to-medium first-person statements (not long article pastes)
    if len(text) > 500:
        return None
    matched_patterns = []
    event_type = None
    mutation_target = None
    for pattern, evt_type, target in _EXEC_DECL_PATTERNS:
        if _re_triage.search(pattern, text_lower):
            matched_patterns.append(pattern)
            if event_type is None:
                event_type = evt_type
                mutation_target = target
    if not matched_patterns:
        return None
    return {
        "intelligence_type": TYPE_EXEC_DECL,
        "confidence": "authoritative",
        "source": "CEO",
        "event_type": event_type,
        "mutation_target": mutation_target,
        "extracted_summary": text.strip(),
        "extracted_entities": [],
        "requires_confirmation": False,
        "auto_mutate": True,
        "matched_patterns": len(matched_patterns),
    }

# ---------------------------------------------------------------------------
# Classifier signal dictionaries
# ---------------------------------------------------------------------------

# Macro signal keywords: each key is a signal category
_MACRO_KEYWORD_GROUPS: dict[str, list[str]] = {
    "consumer_behavior": [
        "consumer", "customer", "spending", "traffic", "visit", "comp sales",
        "comparable sales", "same-store", "same store", "value menu", "trade down",
        "trade-down", "affordability", "wallet", "discretionary", "price sensitivity",
        "parking lot", "drive-thru", "dine-in",
    ],
    "market_signal": [
        "market share", "unit economics", "revenue", "growth", "decline",
        "quarterly", "q1", "q2", "q3", "q4", "earnings", "guidance", "forecast",
        "deployment", "rollout", "expansion", "acquisition", "merger", "ipo",
        "funding", "series", "valuation",
    ],
    "vendor_tech": [
        "pos", "point of sale", "back office", "loyalty", "crm", "online ordering",
        "kds", "kitchen display", "drive-thru ai", "voice ai", "digital menu",
        "payment", "restaurant ai", "automation", "self-order", "kiosk",
        "table management", "reservation",
    ],
    "competitive": [
        "competitor", "competitive", "market position", "wins", "lost", "customer win",
        "customer loss", "share gain", "displacement", "replaced", "vendor shift",
        "partnership", "integration",
    ],
    "industry_trend": [
        "industry", "sector", "hospitality", "restaurant", "qsr", "fast food",
        "fast casual", "casual dining", "food service", "foodservice", "franchise",
        "operator", "multi-unit", "enterprise", "nra", "technomic",
    ],
}
_MACRO_KEYWORDS: set[str] = {
    kw for kws in _MACRO_KEYWORD_GROUPS.values() for kw in kws
}

# Known micro graph entities — activation terms per artifact_id
_MICRO_GRAPH_ENTITY_TERMS: dict[str, list[str]] = {
    "micro_graph:mcdonalds_us_ops": [
        "mcdonald", "mcd", "nsn", "golden arches", "franchisee",
        "storetech", "store tech", "field office", "coop", "co-op",
        "rfm", "otm", "stim", "fbp", "otp",
    ],
}

# Topology-indicating terms that suggest micro graph data when paired with an entity
_TOPOLOGY_TERMS: list[str] = [
    "operator", "franchise", "franchisee", "store count", "stores", "location",
    "org chart", "organization", "hierarchy", "territory", "region", "market",
    "field office", "district", "area", "division", "contact", "roster",
    "site", "unit count", "deployment count", "customer list", "account list",
    "vendor list", "technology stack",
]

# Candidate entity terms for new micro graph creation (not yet in registry)
_MICRO_GRAPH_CANDIDATE_ENTITIES: dict[str, list[str]] = {
    "par_technology": ["par technology", "par tech", "par corp", "par inc"],
    "toast_pos": ["toast", "toast pos", "toasttab"],
    "global_payments": ["global payments", "cayan", "heartland"],
    "foods_connected": ["foods connected", "foodsconnected"],
    "olo": ["olo", "olo inc"],
    "lightspeed": ["lightspeed"],
    "oracle_hospitality": ["oracle hospitality", "micros", "opera"],
    "ncr_voyix": ["ncr", "voyix", "aloha"],
    "revel_systems": ["revel"],
}

# RI trigger patterns — indicate person-level relationship signals
_RI_TRIGGERS: list[str] = [
    r"\bjoined\b", r"\bleft\b", r"\bpromoted\b", r"\bhired\b",
    r"\bappointed\b", r"\bstepped down\b", r"\bnamed\b",
    r"\bretiring\b", r"\bretired\b", r"\bsuccessor\b", r"\bsuccession\b",
    r"\bintroduced\b", r"\bintro\b", r"\bmet with\b", r"\bmeeting with\b",
    r"\breached out\b", r"\bfollowed up\b", r"\bresponded\b",
    r"\bno response\b", r"\bawaiting\b",
    r"\bconnection\b", r"\bnetwork\b", r"\breferral\b",
    r"\borganization change\b", r"\borg change\b", r"\bteam change\b",
    r"\bexecutive change\b", r"\bleadership change\b",
    r"\breply\b", r"\bemail from\b", r"\bmessage from\b", r"\bcall with\b",
]
_RI_TRIGGER_RE = re.compile("|".join(_RI_TRIGGERS), re.IGNORECASE)

# Strategic memory triggers — indicate user thesis / positioning / watchlist
_STRATEGIC_TRIGGERS: list[str] = [
    r"\bvalidates\b", r"\bthis confirms\b", r"\bmy thesis\b",
    r"\bwatchlist\b", r"\bwatch list\b",
    r"\bstrategic priority\b", r"\bstrategic focus\b",
    r"\bnote this\b", r"\bremember this\b",
    r"\bi believe\b", r"\bi think\b", r"\bin my view\b",
    r"\bthis supports\b", r"\bthis aligns\b", r"\bthis validates\b",
    r"\bindustry intelligence\b", r"\bmarket intelligence\b",
    r"\bcompetitive intelligence\b",
    r"\badd to\b.*\bassessment\b", r"\bassessment\b",
    r"\bpositioning\b", r"\bdifferentiation\b",
]
_STRATEGIC_TRIGGER_RE = re.compile("|".join(_STRATEGIC_TRIGGERS), re.IGNORECASE)

# Minimum keyword hit counts for macro signal classification
_MACRO_MIN_HITS = 2

# ---------------------------------------------------------------------------
# Input format detection (RB 9.26)
# ---------------------------------------------------------------------------

# Recognized input formats
FORMAT_LINKEDIN_POST = "linkedin_post"
FORMAT_LINKEDIN_NEWSLETTER = "linkedin_newsletter"
FORMAT_NEWSLETTER = "newsletter"
FORMAT_EMAIL_THREAD = "email_thread"
FORMAT_TRANSCRIPT = "transcript"
FORMAT_ARTICLE = "article"
FORMAT_SOCIAL_POST = "social_post"
FORMAT_PASTE = "paste"  # unrecognized / generic

KNOWN_FORMATS = frozenset({
    FORMAT_LINKEDIN_POST, FORMAT_LINKEDIN_NEWSLETTER, FORMAT_NEWSLETTER,
    FORMAT_EMAIL_THREAD, FORMAT_TRANSCRIPT, FORMAT_ARTICLE,
    FORMAT_SOCIAL_POST, FORMAT_PASTE,
})

# processing_disposition: what the GPT should do after triage
#   process_all_streams  — auto-execute all signal recordings; only baseline
#                          mutations require confirmation
#   confirm_ri_mutations — RI signals present but email/transcript provenance
#                          needs operator review before baseline writes
#   confirm_all          — unknown format; be conservative
_FORMAT_DISPOSITION: dict[str, str] = {
    FORMAT_LINKEDIN_POST:       "process_all_streams",
    FORMAT_LINKEDIN_NEWSLETTER: "process_all_streams",
    FORMAT_NEWSLETTER:          "process_all_streams",
    FORMAT_EMAIL_THREAD:        "confirm_ri_mutations",
    FORMAT_TRANSCRIPT:          "confirm_ri_mutations",
    FORMAT_ARTICLE:             "process_all_streams",
    FORMAT_SOCIAL_POST:         "process_all_streams",
    FORMAT_PASTE:               "confirm_all",
}

# Whether the format qualifies for auto-processing (no extra confirmation gate)
_FORMAT_AUTO_PROCESS: dict[str, bool] = {
    FORMAT_LINKEDIN_POST:       True,
    FORMAT_LINKEDIN_NEWSLETTER: True,
    FORMAT_NEWSLETTER:          True,
    FORMAT_EMAIL_THREAD:        False,
    FORMAT_TRANSCRIPT:          False,
    FORMAT_ARTICLE:             True,
    FORMAT_SOCIAL_POST:         True,
    FORMAT_PASTE:               False,
}

# How much to boost RI confidence when a LinkedIn post format is detected
# and an author is present — even without explicit RI trigger words
_FORMAT_IMPLICIT_RI: frozenset[str] = frozenset({
    FORMAT_LINKEDIN_POST,
    FORMAT_SOCIAL_POST,
})


def _detect_format(text: str) -> tuple[str, float]:
    """Detect the format/medium of the input text.

    Returns:
        (format_name, confidence)  where confidence is 0.0–1.0.

    Scoring is additive — each format accumulates signal points.  The format
    with the highest score wins.  Minimum score threshold avoids false positives
    on short or ambiguous pastes.
    """
    text_lower = text.lower()

    scores: dict[str, int] = {k: 0 for k in KNOWN_FORMATS if k != FORMAT_PASTE}

    # ── LinkedIn post signals ──────────────────────────────────────────────
    if re.search(r"linkedin\.com/in/", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 3
        scores[FORMAT_LINKEDIN_NEWSLETTER] += 1
    if re.search(r"linkedin\.com/company/", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 2
    if re.search(r"linkedin\.com", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 1
        scores[FORMAT_LINKEDIN_NEWSLETTER] += 1
    # Degree markers: "• 1st", "• 2nd", "1st •"
    if re.search(r"[•·]\s*(1st|2nd|3rd|following)\b", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 4
    if re.search(r"\b(1st|2nd|3rd)\s*[•·]", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 4
    # Reaction/engagement lines typical of copied LI posts
    if re.search(r"\b\d[\d,]*\s+(reaction|comment|repost|like)s?\b", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_POST] += 3
    # Action buttons copied from UI
    if re.search(r"^(Like|Comment|Repost|Send)\s*$", text, re.MULTILINE):
        scores[FORMAT_LINKEDIN_POST] += 2
    # Hashtags are common in LI posts
    if re.search(r"#[A-Za-z][A-Za-z0-9]{2,}", text):
        scores[FORMAT_LINKEDIN_POST] += 1
    # "• [headline/role]" pattern under an author name
    if re.search(r"[•·]\s+[A-Z][^•\n]{5,60}[•·]", text):
        scores[FORMAT_LINKEDIN_POST] += 1

    # ── LinkedIn newsletter signals ────────────────────────────────────────
    if re.search(r"linkedin\s+(newsletter|pulse)", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_NEWSLETTER] += 5
    if re.search(r"(issue|edition|vol\.?)\s*#?\s*\d+", text, re.IGNORECASE) and \
            re.search(r"linkedin\.com", text, re.IGNORECASE):
        scores[FORMAT_LINKEDIN_NEWSLETTER] += 3

    # ── Generic newsletter signals ─────────────────────────────────────────
    if "unsubscribe" in text_lower:
        scores[FORMAT_NEWSLETTER] += 5
        scores[FORMAT_LINKEDIN_NEWSLETTER] += 2  # could be a LI newsletter
    if "view in browser" in text_lower or "view online" in text_lower:
        scores[FORMAT_NEWSLETTER] += 3
    if "forward to a friend" in text_lower or "share this email" in text_lower:
        scores[FORMAT_NEWSLETTER] += 3
    if re.search(r"you'?re\s+receiving\s+this", text, re.IGNORECASE):
        scores[FORMAT_NEWSLETTER] += 3
    if re.search(r"(issue|edition)\s*#\s*\d+", text, re.IGNORECASE):
        scores[FORMAT_NEWSLETTER] += 2
    if re.search(
        r"(weekly|monthly|daily|bi-weekly)\s+(issue|edition|newsletter|digest)",
        text, re.IGNORECASE
    ):
        scores[FORMAT_NEWSLETTER] += 2
    if re.search(r"\bsponsored\s+by\b", text, re.IGNORECASE):
        scores[FORMAT_NEWSLETTER] += 1
    if "subscribe" in text_lower and len(text) > 400:
        scores[FORMAT_NEWSLETTER] += 1

    # ── Email thread signals ───────────────────────────────────────────────
    if re.search(r"^(From|To|Subject|Date|Cc|Bcc)\s*:", text, re.MULTILINE):
        scores[FORMAT_EMAIL_THREAD] += 4
    if re.search(r"^(Re:|Fwd:|FW:|FWD:)\s+\S", text, re.MULTILINE):
        scores[FORMAT_EMAIL_THREAD] += 3
    if re.search(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", text):
        scores[FORMAT_EMAIL_THREAD] += 2
    if re.search(r"On .{5,60}wrote:", text, re.DOTALL):
        scores[FORMAT_EMAIL_THREAD] += 3
    if re.search(r"-----\s*Original Message\s*-----", text, re.IGNORECASE):
        scores[FORMAT_EMAIL_THREAD] += 3

    # ── Transcript signals ─────────────────────────────────────────────────
    if re.search(r"\[\d{1,2}:\d{2}(:\d{2})?\]", text):
        scores[FORMAT_TRANSCRIPT] += 5
    if re.search(r"^\s*Speaker\s*\d*\s*:", text, re.MULTILINE | re.IGNORECASE):
        scores[FORMAT_TRANSCRIPT] += 4
    if re.search(r"^[A-Z][a-z]+\s+[A-Z][a-z]+\s*:", text, re.MULTILINE):
        # e.g. "John Smith: [speech]"
        matches = re.findall(r"^[A-Z][a-z]+\s+[A-Z][a-z]+\s*:", text, re.MULTILINE)
        if len(matches) >= 2:
            scores[FORMAT_TRANSCRIPT] += 3
    if re.search(r"^>>\s+\S", text, re.MULTILINE):
        scores[FORMAT_TRANSCRIPT] += 2
    if re.search(r"\[inaudible\]|\[crosstalk\]|\[laughter\]", text, re.IGNORECASE):
        scores[FORMAT_TRANSCRIPT] += 3

    # ── Article / blog post signals ────────────────────────────────────────
    if re.search(r"\bBy\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b", text):
        scores[FORMAT_ARTICLE] += 3
    if re.search(r"\b(Published|Updated|Posted)\s*(on|:)?\s+[A-Z][a-z]+ \d{1,2}", text, re.IGNORECASE):
        scores[FORMAT_ARTICLE] += 2
    if re.search(r"\|.{3,40}\|", text):  # "Name | Publication | Date" bylines
        scores[FORMAT_ARTICLE] += 1
    if re.search(r"\bRead\s+more\b", text, re.IGNORECASE):
        scores[FORMAT_ARTICLE] += 1
    # Long paragraphs with no structural markup → article body
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 200]
    if len(paragraphs) >= 2:
        scores[FORMAT_ARTICLE] += 1

    # ── Non-LinkedIn social post signals ──────────────────────────────────
    if re.search(r"twitter\.com|x\.com", text, re.IGNORECASE):
        scores[FORMAT_SOCIAL_POST] += 5
    if re.search(r"^RT @[A-Za-z0-9_]+", text, re.MULTILINE):
        scores[FORMAT_SOCIAL_POST] += 4
    if re.search(r"@[A-Za-z0-9_]{3,}", text) and \
            not re.search(r"linkedin\.com", text, re.IGNORECASE):
        scores[FORMAT_SOCIAL_POST] += 2

    # ── Find winner ────────────────────────────────────────────────────────
    best_format = max(scores, key=lambda k: scores[k])
    best_score = scores[best_format]

    if best_score < 2:
        return FORMAT_PASTE, 0.40  # not enough signal

    # Disambiguate linkedin_newsletter vs linkedin_post vs newsletter
    if best_format == FORMAT_LINKEDIN_POST:
        nl_score = scores[FORMAT_LINKEDIN_NEWSLETTER]
        if nl_score >= scores[FORMAT_LINKEDIN_POST]:
            best_format = FORMAT_LINKEDIN_NEWSLETTER
        elif "unsubscribe" in text_lower and nl_score >= 3:
            best_format = FORMAT_LINKEDIN_NEWSLETTER

    if best_format == FORMAT_NEWSLETTER and scores[FORMAT_LINKEDIN_NEWSLETTER] >= 4:
        best_format = FORMAT_LINKEDIN_NEWSLETTER

    # Confidence calibration
    if best_score >= 8:
        confidence = 0.95
    elif best_score >= 5:
        confidence = 0.85
    elif best_score >= 3:
        confidence = 0.70
    else:
        confidence = 0.55

    return best_format, round(confidence, 2)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_iso() -> str:
    return date.today().isoformat()


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens from text."""
    return re.findall(r"[a-z0-9']+", text.lower())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _triage_id() -> str:
    """Incrementing daily triage ID: TRG-YYYY-MM-DD-NNN."""
    store_path = core.SYSTEM_DIR / ".cache" / "triage_counter.json"
    today = _today_iso()
    try:
        counter = json.loads(store_path.read_text()) if store_path.exists() else {}
    except Exception:  # noqa: BLE001
        counter = {}
    if counter.get("date") != today:
        counter = {"date": today, "n": 0}
    counter["n"] += 1
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(counter))
    except Exception:  # noqa: BLE001
        pass
    return f"TRG-{today}-{counter['n']:03d}"


def _load_artifact_registry() -> dict:
    """Load the general artifact registry (DEFECT-014)."""
    reg_path = core.SYSTEM_DIR / "artifacts" / "registry.json"
    if reg_path.exists():
        try:
            return json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    # Fall back to the micro graph registry
    graph_reg_path = core.SYSTEM_DIR / "graphs" / "index.json"
    if graph_reg_path.exists():
        try:
            data = json.loads(graph_reg_path.read_text(encoding="utf-8"))
            # Normalise: adapt graph registry to artifact registry schema
            artifacts = []
            for g in data.get("graphs") or []:
                artifacts.append({
                    "artifact_id": f"micro_graph:{g.get('graph_slug')}",
                    "artifact_type": "micro_graph",
                    "name": g.get("name"),
                    "entity": g.get("name", "").replace(" US Operations Micro Ecosystem", "").strip(),
                    "entity_aliases": g.get("activation_terms") or [],
                    "status": g.get("status") or "active",
                    "queryable_via": "getMicroGraphSummary",
                    "graph_slug": g.get("graph_slug"),
                    "source_workbook": g.get("source_workbook"),
                    "freshness_date": (g.get("updated_at") or "")[:10] or None,
                    "enrichment_count": 1,
                })
            return {"contract": "rb_intelligence_artifact_registry_v1", "artifacts": artifacts}
        except Exception:  # noqa: BLE001
            pass
    return {"contract": "rb_intelligence_artifact_registry_v1", "artifacts": []}


def _artifact_entity_terms(registry: dict) -> dict[str, list[str]]:
    """Build entity_term → artifact_id lookup from registry."""
    result: dict[str, list[str]] = {}
    for art in registry.get("artifacts") or []:
        aid = art.get("artifact_id") or ""
        terms = [str(t).lower() for t in (art.get("entity_aliases") or [])]
        entity = (art.get("entity") or "").lower()
        if entity:
            terms.append(entity)
        for term in terms:
            result.setdefault(term, [])
            if aid not in result[term]:
                result[term].append(aid)
    # Also include the hardcoded micro graph terms
    for aid, terms in _MICRO_GRAPH_ENTITY_TERMS.items():
        for term in terms:
            result.setdefault(term, [])
            if aid not in result[term]:
                result[term].append(aid)
    return result


# Short-term matching: terms ≤ 4 chars must be whole-word matches, not substrings.
# "par" must not match "comparable"; "mcd" must not match "command".
_SHORT_TERM_THRESHOLD = 4


def _term_in_text(term: str, text_lower: str, token_set: set[str]) -> bool:
    """Return True if term is present in text, with word-boundary enforcement for short terms."""
    if len(term) <= _SHORT_TERM_THRESHOLD:
        # Token-set match is already word-boundary-safe (tokenised on non-alphanumeric boundaries)
        return term in token_set
    return term in text_lower


_EMAIL_HEADER_LINE_RE = re.compile(
    r"^\s*(From|To|Cc|Bcc|Sent|Subject|Date|Reply-To)\s*:", re.IGNORECASE
)


def _strip_email_header_lines(text: str) -> str:
    """Drop email-header-shaped lines (From:/To:/Subject:/...) before name
    extraction. RB-2026-08-28: confirmed live -- a real email screenshot's
    "From: Lenhart, Maggie (WP) <Maggie.Lenhart...>" / "To: Todd Vahlsing"
    header lines, plus its own subject-line title, filled all 6 of
    _extract_names_near_ri_signal's slots before the scan ever reached the
    email BODY, where the actually-relevant contact (Jeff Caplan) was
    mentioned -- he was silently dropped, not merely low-confidence."""
    return "\n".join(
        line for line in text.splitlines() if not _EMAIL_HEADER_LINE_RE.match(line)
    )


_RI_NAME_PROXIMITY_WINDOW = 200  # chars on each side of a trigger match


def _extract_names_near_ri_signal(text: str) -> list[str]:
    """Heuristic: extract capitalized names adjacent to RI trigger phrases.

    RB-2026-08-28: confirmed live -- despite this docstring, the function
    never actually looked at trigger-phrase proximity; it just returned the
    first 6 Title-Case runs in the WHOLE text. On a real email, that meant
    the subject line and sentence-opener capitalization ("Hey Todd.",
    "Just had a quick call...") filled every slot, and the one name genuinely
    adjacent to a real RI trigger ("a quick call WITH Jeff Caplan") never
    appeared at all. Now genuinely proximity-based: for each _RI_TRIGGERS
    match, scan a window around it first; only fall back to the original
    whole-text scan if no trigger match exists (kept for that case only, not
    as a silent default over a smaller pool of good candidates).
    """
    text = _strip_email_header_lines(text)
    name_re = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)*)\b")
    stop_words = {
        "The", "A", "An", "This", "That", "These", "Those", "I", "We", "They",
        "He", "She", "It", "McDonald", "LinkedIn", "Toast", "PAR", "Global",
        "Foods", "Connected", "Technology", "Corp", "Inc", "Ltd",
        "Hey", "Hi", "Hello", "Thanks", "Thank", "Regards", "Best", "Sincerely",
    }

    def _names_in(segment: str) -> list[str]:
        return [n for n in name_re.findall(segment) if n not in stop_words]

    trigger_matches = list(_RI_TRIGGER_RE.finditer(text))
    if trigger_matches:
        trigger_centers = [(m.start() + m.end()) / 2 for m in trigger_matches]
        # Rank every candidate name by DISTANCE to its nearest trigger match,
        # not by where it happens to fall in document order -- a window
        # alone isn't enough when the window is a meaningful fraction of a
        # short document (a title/greeting a few hundred characters from a
        # real trigger would otherwise still out-rank a name a few dozen
        # characters away, exactly the failure this rewrite fixes).
        candidates: list[tuple[float, str]] = []
        seen: set[str] = set()
        for m in name_re.finditer(text):
            name = m.group(1)
            if name in stop_words or name in seen:
                continue
            center = (m.start() + m.end()) / 2
            distance = min(abs(center - tc) for tc in trigger_centers)
            if distance > _RI_NAME_PROXIMITY_WINDOW:
                continue
            seen.add(name)
            candidates.append((distance, name))
        if candidates:
            candidates.sort(key=lambda pair: pair[0])
            return [name for _dist, name in candidates[:6]]

    return _names_in(text)[:6]


# ---------------------------------------------------------------------------
# Four classifiers — each deterministic, no LLM
# ---------------------------------------------------------------------------

def _matched_watchlist_entities(text: str) -> list[str]:
    """Real, known company names actually named in the text -- company
    identity is the single strongest materiality signal RB has, independent
    of whether the text also happens to hit the generic industry-keyword
    buckets below. RB-DEFECT-2026-08-29: a real uploaded screenshot
    ("Starbucks scrapped its AI inventory tool... NomadGo computer vision
    cameras") named two tracked entity_alerts.MANDATORY_ALL entities by name
    and was still classified as noise-adjacent (a stray "i think" trigger)
    because the keyword-only version of _classify_macro below never checked
    for entity identity at all -- "scrapped"/"computer vision" don't happen
    to hit any of the generic keyword buckets. Word-boundary matched,
    case-insensitive; reuses entity_alerts's own ambiguous-name false-
    positive guards (Fourth/Square/Legion) so this doesn't reintroduce
    already-fixed false positives."""
    text_lower = text.lower()
    matched: list[str] = []
    for name in ea.MANDATORY_ALL:
        name_lower = name.lower()
        if not re.search(r"\b" + re.escape(name_lower) + r"\b", text_lower):
            continue
        guard = ea._AMBIGUOUS_ENTITY_FALSE_POSITIVE_RE.get(name_lower)
        if guard and guard.search(text_lower):
            continue
        matched.append(name)
    return matched


def _classify_macro(text: str, token_set: set[str]) -> dict | None:
    """Detect macro/industry/market signal."""
    hits: list[str] = []
    categories_hit: set[str] = set()
    for category, keywords in _MACRO_KEYWORD_GROUPS.items():
        for kw in keywords:
            kw_tokens = set(kw.lower().split())
            if kw_tokens.issubset(token_set) or kw.lower() in text.lower():
                hits.append(kw)
                categories_hit.add(category)

    entity_hits = _matched_watchlist_entities(text)

    if len(hits) < _MACRO_MIN_HITS and not entity_hits:
        return None

    if entity_hits:
        confidence = "high" if len(entity_hits) >= 2 or len(hits) >= 5 else "medium"
    else:
        confidence = "high" if len(hits) >= 5 else "medium" if len(hits) >= 3 else "low"

    summary_parts: list[str] = []
    if entity_hits:
        summary_parts.append(f"Mentions tracked watchlist entities: {', '.join(entity_hits)}.")
    if hits:
        summary_parts.append(
            f"Macro/industry signal detected: {len(hits)} keyword hit(s) across "
            f"categorie(s): {', '.join(sorted(categories_hit))}. "
            f"Key terms: {', '.join(sorted(set(hits))[:8])}."
        )

    return {
        "intelligence_type": TYPE_MACRO,
        "confidence": confidence,
        "entity_scoped": len(entity_hits) == 1,
        "entity_name": entity_hits[0] if len(entity_hits) == 1 else None,
        "extracted_summary": " ".join(summary_parts),
        "extracted_entities": entity_hits + list(sorted(categories_hit)),
        "extracted_signals": list(sorted(set(hits)))[:12],
        "proposed_action": "Classify macro/industry signal via processMacroSignal (POST /macro/signal).",
        "target_endpoint": "/macro/signal",
        "requires_confirmation": True,
        "source_refs": ["triage:macro_classifier"],
    }


def _classify_micro_graph(text: str, token_set: set[str], registry: dict) -> list[dict]:
    """Detect entity-scoped topology data.

    Returns a list because a single input may reference multiple entity scopes
    (e.g., comparing McDonald's and PAR deployment counts).
    """
    results: list[dict] = []
    entity_terms = _artifact_entity_terms(registry)
    text_lower = text.lower()

    # 1. Check registered artifact entities first.
    # Use word-boundary-safe matching for short terms to prevent substring false positives
    # (e.g., "par" matching inside "comparable").
    matched_artifacts: dict[str, str] = {}  # artifact_id → matched_term
    for term, artifact_ids in entity_terms.items():
        if _term_in_text(term, text_lower, token_set):
            for aid in artifact_ids:
                if aid not in matched_artifacts:
                    matched_artifacts[aid] = term

    for artifact_id, matched_term in matched_artifacts.items():
        # Is there also topology data present?
        topology_hit = any(t in text_lower for t in _TOPOLOGY_TERMS)
        intel_type = TYPE_MICRO_ENRICH if topology_hit else TYPE_MICRO_ENRICH
        # Resolve entity name from registry
        entity_name = next(
            (a.get("entity") or a.get("name") for a in registry.get("artifacts") or []
             if a.get("artifact_id") == artifact_id),
            artifact_id
        )
        results.append({
            "intelligence_type": intel_type,
            "confidence": "high" if topology_hit else "medium",
            "entity_scoped": True,
            "entity_name": entity_name,
            "artifact_id": artifact_id,
            "extracted_summary": (
                f"Entity-scoped data detected for existing artifact '{artifact_id}' "
                f"(matched term: '{matched_term}')."
                + (" Topology terms present — enrichment candidate." if topology_hit else "")
            ),
            "extracted_entities": [entity_name],
            "extracted_signals": [matched_term],
            "proposed_action": f"Enrich existing artifact '{artifact_id}' via POST /artifacts/{artifact_id}/enrich.",
            "target_endpoint": f"/artifacts/{artifact_id}/enrich",
            "requires_confirmation": True,
            "source_refs": ["triage:micro_graph_classifier"],
        })

    # 2. Check candidate entities (not yet in registry) — propose new micro graph.
    # Require ≥2 topology terms to avoid false positives from incidental entity mentions
    # in general QSR/restaurant-tech content where "operator" and "market" appear routinely.
    # RB-DEFECT (2026-09-14): this used a raw `t in text_lower` substring check --
    # the same class of bug already fixed once in intelligence_mutation_engine.py
    # for these exact terms (olo/ncr/qu substring collisions), but never applied
    # here. Confirmed live: "olo" is a substring of "technology", so virtually
    # every restaurant-technology capture in this corpus (which routinely also
    # clears the ≥2 topology-term bar) false-positived as an Olo mention. Use
    # the same word-boundary-safe _term_in_text() the registered-artifact branch
    # above already uses.
    for slug, terms in _MICRO_GRAPH_CANDIDATE_ENTITIES.items():
        if any(_term_in_text(t, text_lower, token_set) for t in terms):
            topology_hit_count = sum(1 for t in _TOPOLOGY_TERMS if t in text_lower)
            topology_hit = topology_hit_count >= 2
            if topology_hit:
                entity_name = slug.replace("_", " ").title()
                candidate_id = f"micro_graph:{slug}"
                if candidate_id not in matched_artifacts:
                    results.append({
                        "intelligence_type": TYPE_MICRO_BUILD,
                        "confidence": "medium",
                        "entity_scoped": True,
                        "entity_name": entity_name,
                        "artifact_id": candidate_id,
                        "extracted_summary": (
                            f"Entity-scoped topology data detected for '{entity_name}' — "
                            f"no registered artifact exists. This input is a candidate for "
                            f"a new micro graph artifact."
                        ),
                        "extracted_entities": [entity_name],
                        "extracted_signals": terms[:3],
                        "proposed_action": (
                            f"Register new artifact '{candidate_id}' via POST /artifacts, "
                            f"then build micro graph from this input."
                        ),
                        "target_endpoint": "/artifacts",
                        "requires_confirmation": True,
                        "source_refs": ["triage:micro_graph_classifier"],
                    })

    return results


def _classify_ri(text: str) -> dict | None:
    """Detect person-level relationship signals."""
    matches = _RI_TRIGGER_RE.findall(text)
    if not matches:
        return None
    names = _extract_names_near_ri_signal(text)
    confidence = "high" if len(matches) >= 3 else "medium" if len(matches) >= 1 else "low"
    return {
        "intelligence_type": TYPE_RI,
        "confidence": confidence,
        "entity_scoped": False,
        "entity_name": None,
        "extracted_summary": (
            f"Person-level RI signal detected: {len(matches)} trigger(s): "
            f"{', '.join(sorted(set(m.strip().lower() for m in matches))[:6])}. "
            + (f"Possible named subjects: {', '.join(names)}." if names else "")
        ),
        "extracted_entities": names,
        "extracted_signals": list(sorted(set(m.strip().lower() for m in matches)))[:8],
        "proposed_action": (
            "Extract relationship intelligence via processRelationshipIntake "
            "(POST /relationship/intake)."
        ),
        "target_endpoint": "/relationship/intake",
        "requires_confirmation": True,
        "source_refs": ["triage:ri_classifier"],
    }


def _classify_strategic(text: str) -> dict | None:
    """Detect user thesis / positioning / watchlist notes."""
    matches = _STRATEGIC_TRIGGER_RE.findall(text)
    if not matches:
        return None
    confidence = "high" if len(matches) >= 3 else "medium"
    return {
        "intelligence_type": TYPE_STRATEGIC,
        "confidence": confidence,
        "entity_scoped": False,
        "entity_name": None,
        "extracted_summary": (
            f"Strategic memory / thesis signal detected: "
            f"{len(matches)} trigger(s): "
            f"{', '.join(sorted(set(m.strip().lower() for m in matches))[:6])}."
        ),
        "extracted_entities": [],
        "extracted_signals": list(sorted(set(m.strip().lower() for m in matches)))[:8],
        "proposed_action": (
            "Classify strategic insight via processInsight (POST /insight/intake), "
            "then confirm each mutation via recordStrategicMemory."
        ),
        "target_endpoint": "/insight/intake",
        "requires_confirmation": True,
        "source_refs": ["triage:strategic_classifier"],
    }


def _classify_career_pipeline(text: str) -> dict | None:
    """Detect updates to the user's own active opportunity pipeline
    (RB-DEFECT-037) — job offers, candidate ranking, interview timelines,
    deal stage. These are the user's own career/business status, distinct
    from RI (other people's signals) and strategic memory (market thesis)."""
    text_lower = text.lower()
    matches = [
        phrase for phrase in opportunity_pipeline.CAREER_PIPELINE_TRIGGERS
        if phrase in text_lower
    ]
    if not matches:
        return None
    stage = opportunity_pipeline._classify_stage(text)
    confidence = "high" if stage else "medium"
    return {
        "intelligence_type": TYPE_CAREER,
        "confidence": confidence,
        "entity_scoped": False,
        "entity_name": None,
        "extracted_summary": (
            f"Career/opportunity pipeline signal detected: {len(matches)} trigger(s): "
            f"{', '.join(sorted(set(matches))[:6])}."
            + (f" Detected stage: {stage}." if stage else "")
        ),
        "extracted_entities": [],
        "extracted_signals": list(sorted(set(matches)))[:8],
        "proposed_action": (
            "Record an active opportunity pipeline update via processOpportunityUpdate "
            "(POST /opportunity/update)."
        ),
        "target_endpoint": "/opportunity/update",
        "requires_confirmation": True,
        "source_refs": ["triage:career_pipeline_classifier"],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def triage_input(
    text: str,
    *,
    source_type: str = "paste",
    source_name: str | None = None,
    author_name: str | None = None,
    author_company: str | None = None,
    event_at: str | None = None,
    captured_at: str | None = None,
    registry: dict | None = None,
) -> dict:
    """Identify all intelligence types present in text.

    Returns a triage result with each identified type, extracted signals, and
    proposed mutation.  No writes occur here — requires_confirmation=True on all
    mutation-bearing streams.

    Args:
        text: The input text to classify.
        source_type: "paste" | "file" | "screenshot" | "url" | "transcript"
        source_name: Optional filename, URL, or source label.
        author_name: Optional author/sender name.
        author_company: Optional author/sender company.
        event_at: Optional ISO date when the event occurred.
        captured_at: Optional ISO date when RB captured this (defaults to now).
        registry: Optional pre-loaded artifact registry (for testing).

    Returns:
        {
          "triage_id": "TRG-YYYY-MM-DD-NNN",
          "captured_at": "ISO",
          "input_hash": str,
          "input_summary": str,
          "source_type": str,
          "source_name": str | None,
          "author_name": str | None,
          "author_company": str | None,
          "event_at": str | None,
          "identified_types": [IntelligenceStream],
          "type_count": int,
          "noise_only": bool,
          "persistence_status": "not_persisted",
          "requires_confirmation": bool,
          "processing_order": [type, ...],
        }
    """
    if not text or not text.strip():
        return {
            "triage_id": _triage_id(),
            "captured_at": captured_at or _now_iso(),
            "input_hash": _sha256(""),
            "input_summary": "",
            "source_type": source_type,
            "source_name": source_name,
            "author_name": author_name,
            "author_company": author_company,
            "event_at": event_at,
            "identified_types": [{
                "intelligence_type": TYPE_NOISE,
                "confidence": "high",
                "entity_scoped": False,
                "entity_name": None,
                "extracted_summary": "Empty or whitespace-only input.",
                "extracted_entities": [],
                "extracted_signals": [],
                "proposed_action": "No action.",
                "target_endpoint": None,
                "requires_confirmation": False,
                "source_refs": [],
            }],
            "type_count": 0,
            "noise_only": True,
            "persistence_status": "not_persisted",
            "requires_confirmation": False,
            "processing_order": [],
        }

    if registry is None:
        registry = _load_artifact_registry()

    # ── Format detection (RB 9.26) ─────────────────────────────────────────
    input_format, format_confidence = _detect_format(text)
    auto_process_eligible = _FORMAT_AUTO_PROCESS.get(input_format, False)
    processing_disposition = _FORMAT_DISPOSITION.get(input_format, "confirm_all")

    token_set = set(_tokens(text))
    streams: list[dict] = []

    # ── Executive declaration check (HIGHEST PRIORITY — D3/D4) ────────────────
    # CEO first-person statements are the highest-fidelity source of truth.
    # Run before all other classifiers. If matched, still run other classifiers
    # so intelligence is also recorded, but exec_decl auto_mutate=True.
    exec_decl = classify_executive_declaration(text)
    if exec_decl:
        streams.append(exec_decl)

    # Run all content classifiers
    macro = _classify_macro(text, token_set)
    if macro:
        streams.append(macro)

    micro_streams = _classify_micro_graph(text, token_set, registry)
    streams.extend(micro_streams)

    ri = _classify_ri(text)
    if ri:
        streams.append(ri)

    strategic = _classify_strategic(text)
    if strategic:
        streams.append(strategic)

    career = _classify_career_pipeline(text)
    if career:
        streams.append(career)

    # ── Author-is-known-contact RI injection ──────────────────────────────
    # When author_name is provided, check baseline for a matching contact.
    # If found, inject/upgrade an RI stream regardless of format.
    # This ensures David Tovar posting about McDonald's produces an RI stream
    # even when the input is classified as generic paste.
    if author_name:
        _author_lower = author_name.lower().strip()
        _author_contact_id: str | None = None
        # Whole-token comparison, never raw substring containment: "user" is
        # a literal substring of "Holthouser" and a naive `in` check
        # misattributed a generic placeholder author_name to a real contact
        # (Jim Holthouser) who had nothing to do with the input. Tokens
        # under 2 chars are dropped so single-letter noise can't match.
        _author_tokens = {t for t in _author_lower.split() if len(t) > 1}
        try:
            import rb_core as _rbc_local
            _bl = _rbc_local.load_baseline()
            for _entry in _bl:
                _entry_name = (_entry.get("name") or "").lower()
                _entry_tokens = {t for t in _entry_name.split() if len(t) > 1}
                if not _author_tokens or not _entry_tokens:
                    continue
                if (
                    _author_lower == _entry_name
                    or _author_tokens <= _entry_tokens
                    or _entry_tokens <= _author_tokens
                ):
                    _author_contact_id = _entry.get("id")
                    break
        except Exception:  # noqa: BLE001
            pass
        if _author_contact_id:
            existing_ri = next((s for s in streams if s["intelligence_type"] == TYPE_RI), None)
            if existing_ri:
                # Upgrade to high confidence — author is a known contact
                existing_ri["confidence"] = "high"
                existing_ri["contact_id"] = _author_contact_id
                existing_ri["source_refs"] = list(
                    set(existing_ri.get("source_refs", [])) | {"triage:author_baseline_match"}
                )
            else:
                streams.append({
                    "intelligence_type": TYPE_RI,
                    "confidence": "high",
                    "entity_scoped": False,
                    "entity_name": None,
                    "contact_id": _author_contact_id,
                    "extracted_summary": (
                        f"Author '{author_name}' is a known RB contact (id: {_author_contact_id}). "
                        "This input is an activity signal for that contact."
                    ),
                    "extracted_entities": [author_name],
                    "extracted_signals": ["author_is_known_contact"],
                    "proposed_action": (
                        f"Record activity signal for {author_name} via "
                        "POST /relationship/intake."
                    ),
                    "target_endpoint": "/relationship/intake",
                    "requires_confirmation": True,
                    "source_refs": ["triage:author_baseline_match"],
                })

    # ── Format-aware RI boost (RB 9.26) ───────────────────────────────────
    # A LinkedIn post or social post with a known author is itself a contact
    # signal even if no explicit RI trigger words fired.
    if input_format in _FORMAT_IMPLICIT_RI and author_name:
        existing_ri = next((s for s in streams if s["intelligence_type"] == TYPE_RI), None)
        if existing_ri is None:
            # Inject a low-confidence RI stream from the format context alone
            streams.append({
                "intelligence_type": TYPE_RI,
                "confidence": "low",
                "entity_scoped": False,
                "entity_name": None,
                "extracted_summary": (
                    f"Format-implicit RI signal: {input_format} input from "
                    f"{author_name}"
                    + (f" ({author_company})" if author_company else "")
                    + ". Author's post is itself a contact signal (activity detected)."
                ),
                "extracted_entities": [author_name] + ([author_company] if author_company else []),
                "extracted_signals": [f"format:{input_format}", "author_activity"],
                "proposed_action": (
                    "Extract relationship intelligence via processRelationshipIntake "
                    "(POST /relationship/intake)."
                ),
                "target_endpoint": "/relationship/intake",
                "requires_confirmation": True,
                "auto_process_eligible": False,  # no trigger words → keep confirm
                "source_refs": ["triage:format_implicit_ri"],
            })
        else:
            # Upgrade confidence when format confirms the signal
            if existing_ri.get("confidence") == "low":
                existing_ri["confidence"] = "medium"
            existing_ri["source_refs"] = list(
                set(existing_ri.get("source_refs", [])) | {"triage:format_boost"}
            )

    # ── Author / event context propagation ────────────────────────────────
    if author_name:
        for s in streams:
            if s["intelligence_type"] == TYPE_RI:
                if author_name not in s["extracted_entities"]:
                    s["extracted_entities"].insert(0, author_name)
                s["extracted_summary"] = (
                    f"Author: {author_name}"
                    + (f" ({author_company})" if author_company else "")
                    + ". " + s["extracted_summary"]
                )

    now_iso = captured_at or _now_iso()
    for s in streams:
        s["event_at"] = event_at
        s["event_at_confidence"] = "high" if event_at else "low"
        s["captured_at"] = now_iso
        # Per-stream auto_process_eligible: default from format disposition
        # unless the stream already set a more conservative value
        if "auto_process_eligible" not in s:
            s["auto_process_eligible"] = (
                auto_process_eligible
                # Micro graph enrichment always needs preview+confirm
                and s["intelligence_type"] not in (TYPE_MICRO_BUILD, TYPE_MICRO_ENRICH)
            )

    # ── Noise fallback ─────────────────────────────────────────────────────
    noise_only = len(streams) == 0
    if noise_only:
        streams.append({
            "intelligence_type": TYPE_NOISE,
            "confidence": "medium",
            "entity_scoped": False,
            "entity_name": None,
            "extracted_summary": (
                "No actionable intelligence detected. "
                "Input may be conversational, administrative, or out-of-domain."
            ),
            "extracted_entities": [],
            "extracted_signals": [],
            "proposed_action": "No action required.",
            "target_endpoint": None,
            "requires_confirmation": False,
            "auto_process_eligible": False,
            "source_refs": ["triage:noise"],
            "event_at": event_at,
            "event_at_confidence": "low",
            "captured_at": now_iso,
        })

    # ── Sort by mutation priority ──────────────────────────────────────────
    def _sort_key(s: dict) -> int:
        t = s.get("intelligence_type") or TYPE_NOISE
        try:
            return MUTATION_ORDER.index(t)
        except ValueError:
            return len(MUTATION_ORDER)

    streams.sort(key=_sort_key)

    actionable = [s for s in streams if s["intelligence_type"] != TYPE_NOISE]
    processing_order = [s["intelligence_type"] for s in actionable]

    # ── Trust stats (Sprint E-5) ───────────────────────────────────────────────
    # Standardised block so the GPT can render a consistent Receipt without
    # having to aggregate counts from identified_types itself.
    trust_stats = {
        "streams_detected": len(streams),
        "actionable_streams": len(actionable),
        "mutations_proposed": sum(
            1 for s in actionable if s.get("requires_confirmation")
        ),
        "auto_processable": sum(
            1 for s in actionable if s.get("auto_process_eligible")
        ),
        "noise_only": noise_only,
        "processing_order": processing_order,
        "confidence_by_type": {
            s["intelligence_type"]: s.get("confidence", "medium")
            for s in streams
            if s["intelligence_type"] != TYPE_NOISE
        },
    }

    return {
        "triage_id": _triage_id(),
        "captured_at": now_iso,
        "input_hash": _sha256(text),
        "input_summary": text[:200].replace("\n", " ").strip(),
        # ── Format fields (RB 9.26) ──
        "input_format": input_format,
        "input_format_confidence": format_confidence,
        "auto_process_eligible": auto_process_eligible,
        "processing_disposition": processing_disposition,
        # ── Content fields ──
        "source_type": source_type,
        "source_name": source_name,
        "author_name": author_name,
        "author_company": author_company,
        "event_at": event_at,
        "identified_types": streams,
        "type_count": len(actionable),
        "noise_only": noise_only,
        "persistence_status": "not_persisted",
        "requires_confirmation": not noise_only,
        "processing_order": processing_order,
        # ── Sprint E-5 ──
        "trust_stats": trust_stats,
        "contract": "rb_intelligence_triage_v1",
    }


# ---------------------------------------------------------------------------
# Overlay helper (RB 9.88 — RB-DEFECT-046 Slice 1: universal router)
# ---------------------------------------------------------------------------

def triage_overlay_text(
    text: str,
    *,
    source_type: str,
    source_name: str | None = None,
    registry: dict | None = None,
) -> dict | None:
    """Triage a short overlay snippet (email subject/snippet, calendar
    title/description) and return ``None`` if the result is noise-only.

    Thin noise-filtering wrapper around :func:`triage_input` for batch use by
    ``core.email_overlay()`` / ``core.calendar_overlay()``. Callers should
    load ``registry`` once via :func:`_load_artifact_registry` and reuse it
    across calls to avoid repeated registry loads.
    """
    result = triage_input(
        text,
        source_type=source_type,
        source_name=source_name,
        registry=registry,
    )
    if result.get("noise_only"):
        return None
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Multi-type intelligence triage.")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--text", help="Text to triage.")
    grp.add_argument("--file", help="Path to text file to triage.")
    p.add_argument("--source-type", default="paste",
                   choices=["paste", "file", "screenshot", "url", "transcript"],
                   help="Input source type.")
    p.add_argument("--source-name", help="Optional source label or filename.")
    p.add_argument("--author", help="Optional author/sender name.")
    p.add_argument("--company", help="Optional author/sender company.")
    p.add_argument("--event-at", help="Optional event date (ISO).")
    p.add_argument("--json", action="store_true", help="Output raw JSON.")
    p.add_argument("--cache", action="store_true",
                   help="Write result to system/.cache/intelligence_triage.json.")
    args = p.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = args.text or ""

    result = triage_input(
        text,
        source_type=args.source_type,
        source_name=args.source_name or args.file,
        author_name=args.author,
        author_company=args.company,
        event_at=args.event_at,
    )

    if args.cache:
        core.write_cache("intelligence_triage", result, source="intelligence_triage.py")

    if getattr(args, "json"):
        print(json.dumps(result, indent=2))
        return 0

    fmt = result.get("input_format", "?")
    fmt_conf = result.get("input_format_confidence", 0)
    disp = result.get("processing_disposition", "?")
    auto_ok = result.get("auto_process_eligible", False)
    print(f"Triage ID: {result['triage_id']}")
    print(f"Input format: {fmt} (confidence: {fmt_conf:.0%}) | disposition: {disp} | auto_process: {auto_ok}")
    print(f"Types found: {result['type_count']} | Noise only: {result['noise_only']}")
    print(f"Processing order: {' → '.join(result['processing_order']) or 'n/a'}")
    print()
    for stream in result["identified_types"]:
        itype = stream["intelligence_type"]
        conf = stream["confidence"]
        auto = "auto" if stream.get("auto_process_eligible") else "confirm"
        print(f"  [{itype}] ({conf}) [{auto}]")
        print(f"    {stream['extracted_summary']}")
        if stream.get("proposed_action"):
            print(f"    → {stream['proposed_action']}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
