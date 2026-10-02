"""Pre-processor for source_type=linkedin_screenshot.

Real implementation (replaces the step-2 stub). LinkedIn screenshots
arrive as image uploads — the caller is expected to have OCR'd them
(or pasted the text manually) and is calling this endpoint with the
OCR text in `raw_text`. Future revisions (step 5+) will run OCR locally
when an image path is provided; for now, we work with what we're given
and lean on the existing date/structure heuristics.

What this pre-processor does today:

1. Anchor `event_at` to a visible date in the OCR text when one is
   present. LinkedIn DMs and posts typically render dates as either
   absolute ("May 19", "May 19, 2026"), relative ("1d", "2w", "3mo",
   "1y"), or "Today"/"Yesterday." When the screenshot shows a date,
   `event_at_confidence = "high"` (absolute) or "medium" (relative);
   otherwise `medium` per the design doc default for screenshots.

2. Strip visible LinkedIn UI text — "Send message", "View profile",
   "Open to work" — so the classifier sees the conversational content
   only.

3. Set `source_extras.raw_text_hash` from the OCR text so
   `ri_events.compute_dedupe_key` produces a stable id for the same
   screenshot pasted twice. When the caller has an actual image
   SHA-256 they pass it via `source_ref.image_hash` and that wins.

4. Heuristic person extraction from "Profile of <Name>" / DM thread
   headers ("Conversation with <Name>" / "<Name> · 1st").
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any


MONTHS_RX = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)

ABS_DATE_RX = re.compile(
    rf"\b({MONTHS_RX})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
    re.I,
)

RELATIVE_AGE_RX = re.compile(
    r"\b(\d{1,2})\s*(d|w|mo|m|y)\b",
    re.I,
)

TODAY_RX = re.compile(r"\bToday\b")
YESTERDAY_RX = re.compile(r"\bYesterday\b", re.I)

LINKEDIN_UI_NOISE_RX = re.compile(
    r"\b(Send message|View profile|Open to work|Connect|Follow|"
    r"Add to network|Open to|See more|See translation|Most relevant|"
    r"Like\s+·\s+Reply|Reply\s+·\s+Send|Mute conversation|Active now)\b",
    re.I,
)

CONVERSATION_HEADER_RX = re.compile(
    r"\b(?:Conversation with|Messaging|Chat with)\s+([A-Z][A-Za-z\-' ]{1,40})",
)
PROFILE_HEADER_RX = re.compile(
    r"\bProfile of\s+([A-Z][A-Za-z\-' ]{1,40})",
)
LINKEDIN_NAME_FIRST_RX = re.compile(
    r"^([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,2})\s*·\s*1st",
    re.M,
)


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _parse_captured_at(captured_at: str | None) -> tuple[datetime, str]:
    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            return dt, captured_at
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    return now, now.isoformat(timespec="seconds")


def _resolve_visible_date(text: str, captured_dt: datetime) -> tuple[str | None, str | None, str | None]:
    """Return (iso_date, source_label, confidence)."""
    if not text:
        return None, None, None
    captured_date = captured_dt.date()

    if TODAY_RX.search(text):
        return captured_date.isoformat(), "ocr_today", "high"
    if YESTERDAY_RX.search(text):
        return (captured_date - timedelta(days=1)).isoformat(), "ocr_yesterday", "high"

    m = ABS_DATE_RX.search(text)
    if m:
        month_token = m.group(1).lower()
        # Truncate month_token to leading 3 chars when checking MONTHS map
        month = MONTHS.get(month_token) or MONTHS.get(month_token[:3])
        day = int(m.group(2))
        year_raw = m.group(3)
        if month:
            if year_raw:
                try:
                    iso = datetime(int(year_raw), month, day).date().isoformat()
                    return iso, "ocr_absolute_date", "high"
                except ValueError:
                    pass
            else:
                from datetime import date as _date
                for y in (captured_date.year, captured_date.year - 1):
                    try:
                        cand = _date(y, month, day)
                    except ValueError:
                        continue
                    if cand <= captured_date:
                        return cand.isoformat(), "ocr_absolute_date_year_inferred", "high"

    m = RELATIVE_AGE_RX.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        days_map = {"d": 1, "w": 7, "mo": 30, "m": 30, "y": 365}
        days = days_map.get(unit, 1) * n
        anchor = captured_date - timedelta(days=days)
        return anchor.isoformat(), f"ocr_relative_age:{n}{unit}", "medium"

    return None, None, None


def _strip_linkedin_ui_noise(text: str) -> str:
    if not text:
        return text
    return LINKEDIN_UI_NOISE_RX.sub("", text)


def _extract_people(text: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for rx in (CONVERSATION_HEADER_RX, PROFILE_HEADER_RX, LINKEDIN_NAME_FIRST_RX):
        for m in rx.finditer(text or ""):
            cand = (m.group(1) or "").strip()
            if cand and cand.lower() not in seen and cand.lower() != "todd":
                out.append({"raw": cand, "decision": "propose_new_contact"})
                seen.add(cand.lower())
    return out


def preprocess(req: dict[str, Any]) -> dict[str, Any]:
    captured_dt, captured_iso = _parse_captured_at(req.get("captured_at"))
    raw_text = req.get("raw_text") or req.get("summary") or ""
    cleaned = _strip_linkedin_ui_noise(raw_text)
    source_ref = req.get("source_ref") or {}

    # Date resolution
    explicit_event_at = req.get("event_at")
    if explicit_event_at:
        event_at_iso = explicit_event_at
        event_at_confidence = req.get("event_at_confidence") or "high"
        event_at_source = req.get("event_at_source") or "operator_explicit_event_at"
    else:
        parsed, label, conf = _resolve_visible_date(raw_text, captured_dt)
        if parsed:
            event_at_iso = parsed
            event_at_confidence = conf or "medium"
            event_at_source = label or "ocr_date"
        else:
            event_at_iso = captured_iso[:10]
            event_at_confidence = "medium"  # design-doc default for screenshots
            event_at_source = "linkedin_screenshot_default"

    # Dedupe-stable identifier — caller's image SHA wins.
    image_hash = source_ref.get("image_hash") or source_ref.get("raw_text_hash")
    if not image_hash and raw_text:
        image_hash = "sha256:" + hashlib.sha256(raw_text.strip().encode("utf-8")).hexdigest()

    extracted_people = list(req.get("people") or [])
    seen = {(p.get("raw") or p.get("name") or "").lower() for p in extracted_people}
    for p in _extract_people(raw_text):
        if p["raw"].lower() not in seen:
            extracted_people.append(p)
            seen.add(p["raw"].lower())

    return {
        "text": cleaned,
        "name": req.get("name"),
        "contact_id": req.get("contact_id"),
        "organization": req.get("organization"),
        "opportunity": req.get("opportunity"),
        "event_at": event_at_iso,
        "event_at_confidence": event_at_confidence,
        "event_at_source": event_at_source,
        "captured_at": captured_iso,
        "extracted_people": extracted_people,
        "extracted_companies": req.get("companies") or [],
        "additional_signals": [],
        "source_extras": {
            **source_ref,
            "raw_text_hash": image_hash,
        },
    }
