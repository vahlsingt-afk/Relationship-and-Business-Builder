"""Pre-processor for source_type=recruiting_update.

Real implementation (replaces the step-2 stub). Job:

1. Resolve relative dates ("yesterday", "Friday", "this morning", "last week")
   against captured_at so event_at anchors to when the recruiting event
   actually happened — not to when the operator told RB about it. This
   directly addresses the date-of-intelligence rule in P-021 §"Date-of-
   intelligence gate" and trace T-2026-05-19-006's expectation that
   "yesterday" → 2026-05-18 (Ryan Hildebrand) and "Friday" → 2026-05-15
   (Simin / Hari).
2. Promote event_at_confidence based on how the date was determined:
     - "high"   — operator passed an explicit ISO date in the request.
     - "medium" — parsed from clear relative phrasing.
     - "low"    — no recognizable date phrasing; falls back to captured_at.
3. Surface event_at_source describing the parse path so the daily brief
   can render grounding labels.
4. Pre-populate extracted_people with the recruiter (name passed in OR
   inferred from "the headhunter") and any explicitly-named hiring contact.
5. Set source_extras.role_slug from the opportunity field so
   ri_events.compute_dedupe_key produces a stable recruiting dedupe key.

The actual signal classification still happens in
manual_relationship_intake._detect_signals — the recruiting state-
transition regexes were added there alongside the existing ones.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


WEEKDAY_INDEX = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

# Order matters — more specific patterns first.
RELATIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bthis morning\b", re.I), "this_morning"),
    (re.compile(r"\bthis afternoon\b", re.I), "this_afternoon"),
    (re.compile(r"\bthis evening\b", re.I), "this_evening"),
    (re.compile(r"\btonight\b", re.I), "tonight"),
    (re.compile(r"\btoday\b", re.I), "today"),
    (re.compile(r"\byesterday\b", re.I), "yesterday"),
    (re.compile(r"\bthe day before yesterday\b", re.I), "day_before_yesterday"),
    (re.compile(r"\bearlier this week\b", re.I), "earlier_this_week"),
    (re.compile(r"\blast week\b", re.I), "last_week"),
    (re.compile(r"\bearlier today\b", re.I), "earlier_today"),
    (
        re.compile(
            r"\b(?:on |last |this )?"
            r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
            r"|mon|tue|tues|wed|thu|thurs|fri|sat|sun)\b",
            re.I,
        ),
        "weekday_ref",
    ),
]


def _parse_captured_at(captured_at: str | None) -> tuple[datetime, str]:
    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            return dt, captured_at
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    return now, now.isoformat(timespec="seconds")


def _resolve_relative_date(text: str, captured_dt: datetime) -> tuple[str | None, str | None]:
    """Find the strongest relative-date phrase in `text` and return
    `(event_at_iso, event_at_source)`. Returns (None, None) if nothing
    clear is found."""
    if not text:
        return None, None
    captured_date = captured_dt.date()

    for rx, kind in RELATIVE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        if kind in {"today", "this_morning", "this_afternoon", "this_evening", "tonight", "earlier_today"}:
            return captured_date.isoformat(), f"relative_phrase:{kind}"
        if kind == "yesterday":
            return (captured_date - timedelta(days=1)).isoformat(), "relative_phrase:yesterday"
        if kind == "day_before_yesterday":
            return (captured_date - timedelta(days=2)).isoformat(), "relative_phrase:day_before_yesterday"
        if kind == "earlier_this_week":
            # Pick Monday of the captured-week if today isn't Monday; otherwise yesterday.
            offset = captured_date.weekday()  # Monday=0
            anchor = captured_date - timedelta(days=max(offset, 1))
            return anchor.isoformat(), "relative_phrase:earlier_this_week"
        if kind == "last_week":
            # Wednesday of the prior week — a reasonable median.
            this_monday = captured_date - timedelta(days=captured_date.weekday())
            anchor = this_monday - timedelta(days=4)  # prior Wednesday
            return anchor.isoformat(), "relative_phrase:last_week"
        if kind == "weekday_ref":
            named = m.group(1).lower()
            target_dow = WEEKDAY_INDEX.get(named)
            if target_dow is None:
                continue
            current_dow = captured_date.weekday()
            # The optional "on |last |this " prefix lives INSIDE m.group(0),
            # so inspect the full match rather than the surrounding text.
            full_match = (m.group(0) or "").lower()
            forward_intent = full_match.startswith("this ")
            if forward_intent and target_dow != current_dow:
                # Upcoming occurrence within the current ISO week; if the
                # target weekday already passed this week, fall through to
                # the past-tense default (operator probably meant last week).
                delta = (target_dow - current_dow) % 7
                if delta == 0:
                    delta = 0
                elif delta <= 6 and target_dow > current_dow:
                    anchor = captured_date + timedelta(days=delta)
                    return anchor.isoformat(), f"relative_phrase:this-{named}"
            # Default: most recent prior occurrence (operators usually
            # describe recruiting events in past tense).
            delta = (current_dow - target_dow) % 7
            if delta == 0:
                if forward_intent:
                    delta = 0
                else:
                    delta = 7
            anchor = captured_date - timedelta(days=delta)
            return anchor.isoformat(), f"relative_phrase:{named}"
    return None, None


HEADHUNTER_NAME_RX = re.compile(
    r"\b(headhunter|recruiter)\s+(?:named|called)?\s*([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+)?)\b"
)
SPOKE_TO_NAME_RX = re.compile(
    r"\b(?:talked|spoke|chatted) (?:to|with)\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+)?)\b"
)


def _extract_people(text: str, explicit_name: str | None) -> list[dict]:
    """Best-effort person extraction. Operator-supplied name wins."""
    out: list[dict] = []
    if explicit_name:
        out.append({"raw": explicit_name})
    seen = {(p.get("raw") or "").lower() for p in out}
    for rx in (HEADHUNTER_NAME_RX, SPOKE_TO_NAME_RX):
        m = rx.search(text or "")
        if not m:
            continue
        # SPOKE_TO_NAME_RX captures the name in group 1; HEADHUNTER_NAME_RX in
        # group 2. Pick the last group.
        cand = (m.group(m.lastindex) or "").strip()
        if cand and cand.lower() not in seen:
            out.append({"raw": cand, "decision": "propose_new_contact"})
            seen.add(cand.lower())
    return out


def preprocess(req: dict[str, Any]) -> dict[str, Any]:
    captured_dt, captured_iso = _parse_captured_at(req.get("captured_at"))
    raw_text = req.get("raw_text") or req.get("summary") or ""

    # Date resolution: explicit ISO > operator-supplied event_at > relative parse.
    explicit_event_at = req.get("event_at")
    if explicit_event_at:
        event_at_iso = explicit_event_at
        event_at_confidence = req.get("event_at_confidence") or "high"
        event_at_source = req.get("event_at_source") or "operator_explicit_event_at"
    else:
        parsed, parsed_source = _resolve_relative_date(raw_text, captured_dt)
        if parsed:
            event_at_iso = parsed
            event_at_confidence = "medium"
            event_at_source = parsed_source
        else:
            event_at_iso = captured_iso[:10]
            event_at_confidence = "low"
            event_at_source = "fallback_to_captured_at"

    # role_slug for stable recruiting dedupe key
    role_slug = (
        req.get("opportunity")
        or (req.get("source_ref") or {}).get("role_slug")
        or (req.get("source_ref") or {}).get("opportunity")
    )

    return {
        "text": raw_text,
        "name": req.get("name"),
        "contact_id": req.get("contact_id"),
        "organization": req.get("organization"),
        "opportunity": req.get("opportunity"),
        "event_at": event_at_iso,
        "event_at_confidence": event_at_confidence,
        "event_at_source": event_at_source,
        "captured_at": captured_iso,
        "extracted_people": (req.get("people") or []) + _extract_people(raw_text, req.get("name")),
        "extracted_companies": req.get("companies") or [],
        "additional_signals": [],
        "source_extras": {
            **(req.get("source_ref") or {}),
            "role_slug": role_slug,
        },
    }
