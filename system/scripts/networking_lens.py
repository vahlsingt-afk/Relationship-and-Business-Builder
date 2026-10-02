#!/usr/bin/env python3
"""
networking_lens.py — Proactive Networking Introduction Engine (RB 9.33 / DEFECT-016).

Closes the gap where the CoS captured meeting participants but never asked
"who should Todd introduce these people to?" The lens transforms every
networking interaction from note-taking into relationship orchestration.

Designed to be invoked automatically by relationship_intake.process_relationship_thread()
when a networking context is detected in the input text.

Core flow:
  1. detect_networking_event(text)          → bool + event_type
  2. extract_participants(text)             → list of {name, org, role, wants}
  3. scan_networking_event(text, baseline)  → ranked introduction candidates

Output contract:
  {
    "networking_event_detected": bool,
    "event_type": str,                     # roundtable | hospitality_table | scn | ...
    "participants_analyzed": int,
    "introduction_candidates": [           # people to introduce TO baseline contacts
      {
        "introduce": {name, org, role},
        "to": {id, name, org, role, rc_tier, signal_class},
        "rationale": str,
        "match_type": str,                 # icp_match | referral_match | industry_bridge | mutual_value
        "priority": str,                   # high | medium | low
        "suggested_framing": str,          # one-line Todd-voice intro hook
      }
    ],
    "relationship_bridges": [             # participant-to-participant synergies
      {
        "person_a": {name, org},
        "person_b": {name, org},
        "bridge_rationale": str,
        "priority": str,
      }
    ],
    "no_match_participants": [str],        # names with wants but no baseline match
    "trust_statement": str,
    "generated_at": str,
  }

Usage:
    python3 networking_lens.py --text "Roundtable notes..." --json
    python3 networking_lens.py --text "..." --top-n 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

# ---------------------------------------------------------------------------
# Event type detection
# ---------------------------------------------------------------------------

# Maps event-type label → detection phrases (any match → that event type).
# Checked in order; first match wins.
_EVENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("hospitality_table", [
        "hospitality table", "hospitalitytable", "htm meeting", "htm call",
    ]),
    ("scn_meeting", [
        "scn meeting", "success champions network", "scn call", "scn event",
    ]),
    ("roundtable", [
        "roundtable", "round table", "round-table",
        "industry roundtable", "peer roundtable",
    ]),
    ("virtual_coffee", [
        "virtual coffee", "coffee chat", "networking coffee",
        "zoom coffee", "teams coffee", "intro call", "introductory call",
    ]),
    ("conference", [
        "conference", "summit", "expo", "tradeshow", "trade show", "industry event",
        "nra show", "fast casual", "restaurant technology", "hitec",
    ]),
    ("webinar", [
        "webinar", "webinar attendee", "online session", "virtual session",
    ]),
    ("networking_event", [
        "networking event", "mixer", "reception", "meet-and-greet",
        "speed networking", "alumni event", "industry gathering",
    ]),
    ("referral_conversation", [
        "referral partner", "referral ask", "ideal client profile", "icp ",
        "looking to meet", "who are you looking for", "who do you serve",
        "who do you want to meet", "who should i connect you with",
        "warm introduction", "make an introduction", "connector",
    ]),
]

# Fallback networking signal phrases — if text scores ≥ _FALLBACK_THRESHOLD hits,
# treat as a networking context even without a named event type.
_FALLBACK_KEYWORDS = {
    "introduction", "introduce", "connect", "referral", "ideal client",
    "looking to meet", "who do you know", "networking", "broker", "bridge",
    "who would benefit", "mutual connection", "warm intro",
}
_FALLBACK_THRESHOLD = 2


def detect_networking_event(text: str) -> tuple[bool, str]:
    """Return (is_networking, event_type).

    event_type is one of the keys in _EVENT_PATTERNS, or "general_networking"
    for fallback matches, or "none" when not detected.
    """
    lower = text.lower()
    for event_type, phrases in _EVENT_PATTERNS:
        if any(p in lower for p in phrases):
            return True, event_type

    # Fallback: count distinct networking signal words
    hits = sum(1 for kw in _FALLBACK_KEYWORDS if kw in lower)
    if hits >= _FALLBACK_THRESHOLD:
        return True, "general_networking"

    return False, "none"


# ---------------------------------------------------------------------------
# Participant + want extraction
# ---------------------------------------------------------------------------

# Want-signal phrases that indicate a person is describing who they want to meet.
_WANT_SIGNALS = [
    r"looking to meet\s+(.{5,120}?)(?:\.|,|$)",
    r"wants? to meet\s+(.{5,120}?)(?:\.|,|$)",
    r"hoping to meet\s+(.{5,120}?)(?:\.|,|$)",
    r"ideal client[s]?\s+(?:is|are|include[s]?|:)?\s*(.{5,150}?)(?:\.|,|$)",
    r"icp\s+(?:is|are|:)?\s*(.{5,150}?)(?:\.|,|$)",
    r"looking for\s+(.{5,120}?)(?:\.|,|$)",
    r"seeking\s+(.{5,120}?)(?:\.|,|$)",
    r"want[s]? to connect with\s+(.{5,120}?)(?:\.|,|$)",
    r"referral partner[s]?\s+(?:is|are|would be|:)?\s*(.{5,150}?)(?:\.|,|$)",
    r"partner[s]? with\s+(.{5,120}?)(?:\.|,|$)",
    r"works? with\s+(.{5,120}?)(?:\.|,|$)",
    r"serve[s]?\s+(.{5,120}?)(?:\.|,|$)",
    r"help[s]?\s+(.{5,120}?)(?:\.|,|$)",
    r"introduce[s]? (?:me|us|him|her|them) to\s+(.{5,120}?)(?:\.|,|$)",
]

# Compiled once at module load
_WANT_RE = [re.compile(p, re.IGNORECASE) for p in _WANT_SIGNALS]

# Name detection: "Firstname Lastname" or "Firstname Lastname at Org"
# Org capture requires each word to start with a capital (stops at lowercase verbs/conjunctions).
_NAME_RE = re.compile(
    r"\b([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,25})"
    r"(?:\s+(?:at|from|of|with)\s+"
    r"([A-Z][A-Za-z0-9&',.-]*(?:\s+[A-Z][A-Za-z0-9&',.-]*){0,5}))?"
)

# Known false-positive name-like bigrams to skip
_NAME_STOPWORDS = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
    "New York", "San Francisco", "Los Angeles", "New Jersey", "Las Vegas",
    "United States", "North America", "Vice President", "Chief Operating",
    "Chief Executive", "Chief Financial", "Chief Revenue", "Executive Director",
    "Senior Vice", "Managing Director", "Chief Marketing", "General Manager",
}


def extract_participants(text: str) -> list[dict]:
    """Extract named event participants with their wants from raw text.

    Returns list of:
      {name, org, role, wants: [str], raw_context: str}
    """
    participants: dict[str, dict] = {}

    # Split into sentences to give each name a context window
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)

    for sent in sentences:
        # Find names in this sentence
        for m in _NAME_RE.finditer(sent):
            first, last = m.group(1), m.group(2)
            full = f"{first} {last}"
            if full in _NAME_STOPWORDS or any(w in _NAME_STOPWORDS for w in (first, last)):
                continue
            # Skip titles or known non-name patterns
            if first.lower() in {"the", "this", "that", "our", "your", "their", "any"}:
                continue

            org = m.group(3)

            if full not in participants:
                participants[full] = {
                    "name": full,
                    "org": org.strip() if org else None,
                    "role": None,
                    "wants": [],
                    "raw_context": [],
                }
            elif org and not participants[full]["org"]:
                participants[full]["org"] = org.strip()

            participants[full]["raw_context"].append(sent)

    # Extract wants from context around each participant
    for name, p in participants.items():
        context_text = " ".join(p["raw_context"])
        for pattern in _WANT_RE:
            for m in pattern.finditer(context_text):
                want_phrase = m.group(1).strip().rstrip(".,;")
                if len(want_phrase) >= 5 and want_phrase not in p["wants"]:
                    p["wants"].append(want_phrase)

    # Also scan the full text for each participant's name to catch distant context
    for name, p in participants.items():
        # Find the sentence(s) containing this person's name
        name_re = re.compile(re.escape(name), re.IGNORECASE)
        for sent in sentences:
            if name_re.search(sent) and sent not in p["raw_context"]:
                p["raw_context"].append(sent)
                # Check this sentence for wants too
                for pattern in _WANT_RE:
                    for m in pattern.finditer(sent):
                        want_phrase = m.group(1).strip().rstrip(".,;")
                        if len(want_phrase) >= 5 and want_phrase not in p["wants"]:
                            p["wants"].append(want_phrase)

    # Convert raw_context to string summary
    out = []
    for p in participants.values():
        p["raw_context"] = " ".join(p["raw_context"])[:500]
        out.append(p)
    return out


def _tokenize(text: str) -> set[str]:
    """Normalize text to a set of meaningful lowercase tokens (3+ chars)."""
    if not text:
        return set()
    # Strip punctuation, lowercase, split
    tokens = re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()
    # Remove stopwords and very short tokens
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "have",
        "are", "was", "they", "our", "your", "their", "who", "what",
        "how", "where", "when", "will", "can", "all", "also", "into",
        "more", "some", "such", "than", "then", "its", "but", "not",
        "been", "has", "had", "one", "two", "any", "each", "like",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stopwords}


def _contact_tokens(contact: dict) -> set[str]:
    """Build a token set from a baseline contact's matchable fields."""
    fields = [
        contact.get("name") or "",
        contact.get("current_company") or "",
        contact.get("current_role") or "",
        " ".join(contact.get("circles") or []),
        " ".join(contact.get("tags") or []),
    ]
    return _tokenize(" ".join(fields))


def _tier_weight(contact: dict) -> float:
    """Score multiplier based on relationship tier — prefer warm introductions."""
    sc = contact.get("signal_class") or ""
    tier = contact.get("rc_tier") or ""
    if sc == "RC" and tier == "inner":
        return 3.0   # Inner RC: Todd knows them well enough to ask a favor
    if sc == "RC" and tier == "broader":
        return 2.0   # Broader RC: solid relationship, can make the ask
    if sc == "RC" and tier == "dormant_valuable":
        return 1.2   # Dormant but valuable — reactivation opportunity
    if "RC" in sc:
        return 1.0   # Any RC
    if sc in {"LKI", "LKI:RC"}:
        return 0.4   # Known but not relationship-level
    return 0.2       # VC or unknown


def _match_score(wants: list[str], contact: dict) -> float:
    """Score how well a contact matches a participant's stated wants.

    Returns 0.0–1.0; above 0.15 is a meaningful match.
    """
    if not wants:
        return 0.0

    contact_toks = _contact_tokens(contact)
    if not contact_toks:
        return 0.0

    total_want_toks = 0
    total_matches = 0
    for want in wants:
        want_toks = _tokenize(want)
        if not want_toks:
            continue
        overlap = want_toks & contact_toks
        total_want_toks += len(want_toks)
        total_matches += len(overlap)

    if total_want_toks == 0:
        return 0.0
    return min(1.0, total_matches / total_want_toks)


def _match_type(wants: list[str], contact: dict) -> str:
    """Classify the nature of the match."""
    lower_wants = " ".join(wants).lower()
    role = (contact.get("current_role") or "").lower()
    circles_str = " ".join(contact.get("circles") or []).lower()
    tags_str = " ".join(contact.get("tags") or []).lower()

    # ICP match: participant describes customer/client profile matching contact
    icp_signals = {"client", "customer", "operator", "chain", "restaurant", "franchise"}
    if any(s in lower_wants for s in icp_signals):
        return "icp_match"

    # Referral match: explicit referral ask matches contact role
    referral_signals = {"referral", "partner", "vendor", "supplier", "provider"}
    if any(s in lower_wants for s in referral_signals):
        return "referral_match"

    # Industry bridge: shared sector without explicit ask
    industry_signals = {"restaurant", "hospitality", "technology", "fintech", "payments", "pos"}
    if any(s in role or s in circles_str or s in tags_str for s in industry_signals):
        return "industry_bridge"

    return "mutual_value"


def _priority(score: float) -> str:
    if score >= 0.35:
        return "high"
    if score >= 0.18:
        return "medium"
    return "low"


def _suggested_framing(participant: dict, contact: dict, match_type: str) -> str:
    """Generate a brief Todd-voice introduction hook."""
    p_name = participant["name"].split()[0]  # first name
    p_org = participant.get("org") or "their company"
    c_name = contact["name"].split()[0]
    c_role = contact.get("current_role") or "a contact"
    c_org = contact.get("current_company") or "their org"

    if match_type == "icp_match":
        return (
            f"{p_name}, meet {c_name} — {c_role} at {c_org}. "
            f"Todd thinks {c_org} fits what you described as your ideal client profile."
        )
    if match_type == "referral_match":
        return (
            f"{p_name} at {p_org} is looking for the right referral partner — "
            f"{c_name} at {c_org} could be a strong fit."
        )
    if match_type == "industry_bridge":
        return (
            f"Both {p_name} ({p_org}) and {c_name} ({c_org}) work in the "
            f"restaurant/hospitality space — connecting them creates mutual value."
        )
    return (
        f"Todd sees a potential synergy between {p_name} ({p_org}) and "
        f"{c_name} ({c_org}) — worth a conversation."
    )


# ---------------------------------------------------------------------------
# Participant-to-participant bridge detection
# ---------------------------------------------------------------------------

def _find_participant_bridges(participants: list[dict]) -> list[dict]:
    """Detect complementary participant pairs (A wants what B has and vice versa).

    Returns list of {person_a, person_b, bridge_rationale, priority}.
    """
    bridges = []
    for i, a in enumerate(participants):
        for b in participants[i + 1:]:
            if a["name"] == b["name"]:
                continue
            # Does A's wants overlap with B's context, or vice versa?
            a_wants_toks = _tokenize(" ".join(a["wants"]))
            b_wants_toks = _tokenize(" ".join(b["wants"]))
            b_context_toks = _tokenize(b["raw_context"])
            a_context_toks = _tokenize(a["raw_context"])

            a_wants_b = len(a_wants_toks & b_context_toks)
            b_wants_a = len(b_wants_toks & a_context_toks)

            if a_wants_b >= 2 and b_wants_a >= 1:
                bridges.append({
                    "person_a": {"name": a["name"], "org": a.get("org")},
                    "person_b": {"name": b["name"], "org": b.get("org")},
                    "bridge_rationale": (
                        f"{a['name']} ({a.get('org') or '?'}) described needs that align "
                        f"with what {b['name']} ({b.get('org') or '?'}) offers — "
                        f"and {b['name']} may benefit from {a['name']}'s context in return."
                    ),
                    "priority": "high" if (a_wants_b >= 3 or b_wants_a >= 2) else "medium",
                })
            elif b_wants_a >= 2 and a_wants_b >= 1:
                bridges.append({
                    "person_a": {"name": b["name"], "org": b.get("org")},
                    "person_b": {"name": a["name"], "org": a.get("org")},
                    "bridge_rationale": (
                        f"{b['name']} ({b.get('org') or '?'}) described needs that align "
                        f"with what {a['name']} ({a.get('org') or '?'}) offers."
                    ),
                    "priority": "medium",
                })
    return bridges


# ---------------------------------------------------------------------------
# Main scan function
# ---------------------------------------------------------------------------

def scan_networking_event(
    text: str,
    event_participants: list[dict] | None = None,
    baseline: list[dict] | None = None,
    top_n: int = 8,
    min_match_score: float = 0.10,
) -> dict:
    """Scan a networking event transcript for introduction opportunities.

    Args:
        text: Raw event transcript or notes.
        event_participants: Pre-parsed participants (optional; auto-extracted if None).
        baseline: Preloaded baseline (optional; loaded from disk if None).
        top_n: Max introduction candidates to return.
        min_match_score: Minimum raw match score to consider (before tier weighting).

    Returns the full networking scan result dict (see module docstring).
    """
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    is_networking, event_type = detect_networking_event(text)

    if not is_networking:
        return {
            "networking_event_detected": False,
            "event_type": "none",
            "participants_analyzed": 0,
            "introduction_candidates": [],
            "relationship_bridges": [],
            "no_match_participants": [],
            "trust_statement": "No networking event detected in this input.",
            "generated_at": generated_at,
        }

    if baseline is None:
        baseline = core.load_baseline()

    if event_participants is None:
        event_participants = extract_participants(text)

    # Deduplicate by name
    seen_names: set[str] = set()
    unique_participants: list[dict] = []
    for p in event_participants:
        if p["name"] not in seen_names:
            seen_names.add(p["name"])
            unique_participants.append(p)

    # Build introduction candidates: for each participant with wants, score all baseline contacts
    all_candidates: list[dict] = []
    no_match: list[str] = []

    for participant in unique_participants:
        if not participant.get("wants"):
            continue

        scored: list[tuple[float, dict]] = []
        for contact in baseline:
            raw_score = _match_score(participant["wants"], contact)
            if raw_score < min_match_score:
                continue
            weighted = raw_score * _tier_weight(contact)
            scored.append((weighted, contact))

        if not scored:
            no_match.append(participant["name"])
            continue

        # Sort and take top-3 per participant
        scored.sort(key=lambda x: x[0], reverse=True)
        for weighted_score, contact in scored[:3]:
            mtype = _match_type(participant["wants"], contact)
            all_candidates.append({
                "introduce": {
                    "name": participant["name"],
                    "org": participant.get("org"),
                    "role": participant.get("role"),
                },
                "to": {
                    "id": contact.get("id"),
                    "name": contact.get("name"),
                    "org": contact.get("current_company"),
                    "role": contact.get("current_role"),
                    "rc_tier": contact.get("rc_tier"),
                    "signal_class": contact.get("signal_class"),
                },
                "rationale": (
                    f"{participant['name']} ({participant.get('org') or '?'}) "
                    f"expressed interest in: {'; '.join(participant['wants'][:2])}. "
                    f"{contact.get('name')} ({contact.get('current_company') or '?'}) "
                    f"matches on {_match_type(participant['wants'], contact).replace('_', ' ')}."
                ),
                "match_type": mtype,
                "priority": _priority(weighted_score),
                "weighted_score": round(weighted_score, 3),
                "suggested_framing": _suggested_framing(participant, contact, mtype),
            })

    # Global sort by weighted_score, deduplicate (same introduce+to pair), take top_n
    all_candidates.sort(key=lambda c: c["weighted_score"], reverse=True)
    seen_pairs: set[tuple[str, str | None]] = set()
    deduped: list[dict] = []
    for c in all_candidates:
        pair = (c["introduce"]["name"], c["to"].get("id"))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(c)
        if len(deduped) >= top_n:
            break

    bridges = _find_participant_bridges(unique_participants)

    n_candidates = len(deduped)
    n_participants = len(unique_participants)
    n_with_wants = sum(1 for p in unique_participants if p.get("wants"))

    if n_candidates > 0:
        high_count = sum(1 for c in deduped if c["priority"] == "high")
        trust_statement = (
            f"Networking lens: {n_candidates} introduction candidate(s) identified "
            f"across {n_with_wants} participant(s) with stated wants "
            f"({high_count} high priority). "
            + (f"{len(bridges)} participant bridge(s) detected." if bridges else "")
        )
    elif n_with_wants > 0:
        trust_statement = (
            f"Networking lens: {n_with_wants} participant(s) with stated wants — "
            f"no strong baseline matches found. Consider expanding the watch list."
        )
    else:
        trust_statement = (
            f"Networking lens: {n_participants} participant(s) identified but no "
            f"explicit wants detected. Surface contact profiles for manual review."
        )

    # Strip internal scoring field from final output
    for c in deduped:
        c.pop("weighted_score", None)

    return {
        "networking_event_detected": True,
        "event_type": event_type,
        "participants_analyzed": n_participants,
        "participants_with_wants": n_with_wants,
        "introduction_candidates": deduped,
        "relationship_bridges": bridges,
        "no_match_participants": no_match,
        "trust_statement": trust_statement,
        "generated_at": generated_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Networking Introduction Engine — scan event transcripts for intro opportunities.",
    )
    p.add_argument("--text", help="Raw event transcript text.")
    p.add_argument("--file", help="Path to a text file containing the transcript.")
    p.add_argument("--top-n", type=int, default=8,
                   help="Max introduction candidates to return (default: 8).")
    p.add_argument("--min-score", type=float, default=0.10,
                   help="Minimum match score threshold (default: 0.10).")
    p.add_argument("--json", action="store_true",
                   help="Emit full JSON output.")
    p.add_argument("--detect-only", action="store_true",
                   help="Only report whether a networking event was detected.")
    args = p.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        p.error("Provide --text or --file.")

    if args.detect_only:
        detected, event_type = detect_networking_event(text)
        print(json.dumps({"detected": detected, "event_type": event_type}))
        return 0

    result = scan_networking_event(text, top_n=args.top_n, min_match_score=args.min_score)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    # Human-readable summary
    print(f"\nNetworking Introduction Engine")
    print(f"  Event detected: {result['networking_event_detected']} ({result['event_type']})")
    print(f"  Participants:   {result['participants_analyzed']} analyzed, "
          f"{result.get('participants_with_wants', 0)} with stated wants")
    print(f"  Trust:          {result['trust_statement']}")

    if result["introduction_candidates"]:
        print(f"\n  Introduction Candidates ({len(result['introduction_candidates'])}):")
        for i, c in enumerate(result["introduction_candidates"], 1):
            intro = c["introduce"]
            to = c["to"]
            print(f"\n  {i}. {intro['name']} ({intro.get('org') or '?'})")
            print(f"     → {to['name']} ({to.get('org') or '?'}) "
                  f"[{to.get('rc_tier') or to.get('signal_class') or '?'}]")
            print(f"     Type:     {c['match_type'].replace('_', ' ')}")
            print(f"     Priority: {c['priority']}")
            print(f"     Why:      {c['rationale'][:120]}")
            print(f"     Framing:  {c['suggested_framing'][:120]}")

    if result["relationship_bridges"]:
        print(f"\n  Relationship Bridges ({len(result['relationship_bridges'])}):")
        for b in result["relationship_bridges"]:
            pa, pb = b["person_a"], b["person_b"]
            print(f"  ↔ {pa['name']} ({pa.get('org') or '?'}) "
                  f"↔ {pb['name']} ({pb.get('org') or '?'})  [{b['priority']}]")
            print(f"    {b['bridge_rationale'][:120]}")

    if result["no_match_participants"]:
        print(f"\n  No-match participants (wants stated but no baseline candidate found):")
        for name in result["no_match_participants"]:
            print(f"    - {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
