"""Pre-processor for source_type ∈ {fathom_manual_paste, zoom_manual_paste}.

Real implementation (replaces the step-2 stub). Job:

1. Anchor `event_at` to the **meeting** date, not the upload/paste date.
   P-020 §"Date-of-intelligence rule" and P-021's date-of-intelligence
   gate both require this — a transcript pasted today for a meeting from
   April belongs to April's relationship timeline. Trace T-2026-05-19-005
   explicitly fails the test if the assistant treats the upload date as
   the meeting date.

   Resolution order:
     a. Explicit `event_at` in the request (operator override).
     b. ISO date in the title ("- 2026-05-19", "May 19, 2026", etc.).
     c. Short month-day in the title disambiguated against captured_at
        ("- May 19" with captured_at in 2026 → "2026-05-19"). If the
        resulting date is in the future relative to captured_at, fall back
        to the prior year.
     d. Fallback: captured_at (with event_at_confidence="low").

2. Extract participants from speaker labels in the transcript body. Most
   notetaker exports use lines like "Olivia Nielsen: ..." or
   "[00:12:34] Olivia Nielsen ..." — pulling distinct speaker names gives
   us the participant set without needing the notetaker's metadata block.

3. Surface company hints inferred from the title (e.g., "Todd <> PerfectHire
   - QSR Platform Review" → company "PerfectHire") so the existing
   manual_relationship_intake.assess() classifier knows what organization
   the conversation is about.

4. Set `event_at_confidence`:
     - "high"   when title carries an ISO or unambiguous month-day-year.
     - "high"   when title month-day disambiguates cleanly (within last 365 days).
     - "medium" when only speaker labels indicated the meeting timing.
     - "low"    when nothing better than captured_at could be found.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any


MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

ISO_DATE_RX = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
LONG_DATE_RX = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b",
    re.I,
)
SLASH_DATE_RX = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")

# Speaker label patterns common to Fathom/Zoom/Otter/Fireflies exports.
SPEAKER_LINE_PATTERNS = (
    # "Olivia Nielsen: text" or "Olivia Nielsen  text"
    re.compile(r"^([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,2})\s*[:—\-]\s+\S", re.M),
    # "[00:12:34] Olivia Nielsen" (timestamp prefix)
    re.compile(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,2})\b", re.M),
    # "Olivia Nielsen (00:12:34):" — Fathom-style
    re.compile(r"^([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+){0,2})\s*\(\d{1,2}:\d{2}", re.M),
)

# Words that look like proper nouns to the speaker-pattern regex but are
# almost never people. We filter these out before claiming a participant.
SPEAKER_STOPWORDS = {
    "todd", "operator", "speaker", "guest", "host", "moderator",
    "the meeting", "meeting", "agenda", "summary",
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


def _parse_date_from_title(title: str, captured_dt: datetime) -> tuple[str | None, str | None, str | None]:
    """Return (iso_date, source_label, confidence) parsed from title."""
    if not title:
        return None, None, None
    captured_date = captured_dt.date()

    m = ISO_DATE_RX.search(title)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            iso = date(y, mo, d).isoformat()
            return iso, "title_iso_date", "high"
        except ValueError:
            pass

    m = LONG_DATE_RX.search(title)
    if m:
        month_name = m.group(1).lower()
        d = int(m.group(2))
        y_raw = m.group(3)
        month = MONTHS.get(month_name)
        if month:
            if y_raw:
                y = int(y_raw)
                try:
                    iso = date(y, month, d).isoformat()
                    return iso, f"title_long_date:{month_name}", "high"
                except ValueError:
                    pass
            else:
                # No year in the title — disambiguate against captured year.
                for y in (captured_date.year, captured_date.year - 1):
                    try:
                        cand = date(y, month, d)
                    except ValueError:
                        continue
                    if cand <= captured_date:
                        return cand.isoformat(), f"title_long_date:{month_name}_year_inferred", "high"
                # Still in the future — accept anyway as upcoming meeting.
                try:
                    cand = date(captured_date.year, month, d)
                    return cand.isoformat(), f"title_long_date:{month_name}_year_inferred_future", "medium"
                except ValueError:
                    pass

    m = SLASH_DATE_RX.search(title)
    if m:
        try:
            mo = int(m.group(1)); d = int(m.group(2))
            y_raw = m.group(3)
            y = (
                int(y_raw) if y_raw and len(y_raw) == 4 else
                (2000 + int(y_raw)) if y_raw else captured_date.year
            )
            iso = date(y, mo, d).isoformat()
            label = "title_slash_date_with_year" if y_raw else "title_slash_date_year_inferred"
            confidence = "high" if y_raw else "medium"
            return iso, label, confidence
        except (ValueError, TypeError):
            pass

    return None, None, None


# Company-hint extraction from titles like:
#   "Todd <> PerfectHire - QSR Platform Review - May 19"
#   "Todd / Maho - Next Steps"
#   "PerfectHire | Discovery Call"
TITLE_COMPANY_RX = re.compile(
    r"(?:[Tt]odd\s*(?:<>|/|with|x|\|)\s*)?"
    r"([A-Z][A-Za-z0-9&'\-]+(?:\s+[A-Z][A-Za-z0-9&'\-]+)?)"
    r"\s*(?:[-—|]|$)"
)


def _company_hint_from_title(title: str) -> str | None:
    if not title:
        return None
    m = TITLE_COMPANY_RX.search(title)
    if not m:
        return None
    candidate = m.group(1).strip()
    # Filter out spurious matches.
    if candidate.lower() in {"todd", "meeting", "call", "review", "discussion", "qsr"}:
        return None
    return candidate


def _extract_speakers(text: str) -> list[str]:
    """Pull unique speaker names from the transcript body."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for rx in SPEAKER_LINE_PATTERNS:
        for m in rx.finditer(text):
            name = m.group(1).strip()
            lower = name.lower()
            if lower in seen or lower in SPEAKER_STOPWORDS:
                continue
            # Discard short ALL-CAPS noise like "I" or "QSR".
            if len(name) <= 2:
                continue
            seen.add(lower)
            found.append(name)
    return found


def preprocess(req: dict[str, Any]) -> dict[str, Any]:
    captured_dt, captured_iso = _parse_captured_at(req.get("captured_at"))
    raw_text = req.get("raw_text") or req.get("summary") or ""
    source_ref = req.get("source_ref") or {}
    title = source_ref.get("title") or ""

    # Date resolution.
    explicit_event_at = req.get("event_at")
    if explicit_event_at:
        event_at_iso = explicit_event_at
        event_at_confidence = req.get("event_at_confidence") or "high"
        event_at_source = req.get("event_at_source") or "operator_explicit_event_at"
    else:
        parsed_iso, parsed_label, parsed_confidence = _parse_date_from_title(title, captured_dt)
        if parsed_iso:
            event_at_iso = parsed_iso
            event_at_confidence = parsed_confidence or "high"
            event_at_source = parsed_label or "title_parse"
        else:
            event_at_iso = captured_iso[:10]
            event_at_confidence = "low"
            event_at_source = "fallback_to_captured_at"

    speakers = _extract_speakers(raw_text)
    company_hint = req.get("organization") or _company_hint_from_title(title)

    extracted_people = list(req.get("people") or [])
    seen_raw = {(p.get("raw") or p.get("name") or "").lower() for p in extracted_people}
    for s in speakers:
        if s.lower() in seen_raw or s.lower() == "todd":
            continue
        extracted_people.append({"raw": s, "decision": "propose_new_contact"})
        seen_raw.add(s.lower())

    extracted_companies = list(req.get("companies") or [])
    if company_hint:
        seen_co = {(c.get("raw") or c.get("name") or "").lower() for c in extracted_companies}
        if company_hint.lower() not in seen_co:
            extracted_companies.append({"raw": company_hint, "decision": "propose_new_company"})

    return {
        "text": raw_text,
        "name": req.get("name"),
        "contact_id": req.get("contact_id"),
        "organization": company_hint,
        "opportunity": req.get("opportunity"),
        "event_at": event_at_iso,
        "event_at_confidence": event_at_confidence,
        "event_at_source": event_at_source,
        "captured_at": captured_iso,
        "extracted_people": extracted_people,
        "extracted_companies": extracted_companies,
        "additional_signals": [],
        "source_extras": {
            **source_ref,
            "title": title,
            "speakers": speakers,
        },
    }
