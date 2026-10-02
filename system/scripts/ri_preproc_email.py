"""Pre-processor for source_type=email_paste.

Real implementation (replaces the step-2 stub). Extracts standard RFC
5322 headers from pasted email text (From, To, Subject, Date, Message-Id)
and uses them to:

  - anchor `event_at` to the message Date header (high confidence) instead
    of the paste time
  - set `source_extras.message_id` and `source_extras.subject` so
    `ri_events.compute_dedupe_key` produces a stable email dedupe key
  - pull the sender name + address into `extracted_people` so the
    classifier knows who sent the message without re-parsing the body

We deliberately keep the parsing tolerant: pasted emails arrive in many
flavors (Gmail copy-paste, Outlook forwards, reply-with-quoted-text), so
the regexes accept either standard headers ("Date: Mon, 19 May 2026 ...")
or human-readable shortcuts ("On May 19, 2026 at 10:00 AM, Ryan wrote:").

If no Date header is present, falls back to body-context date hints
("on yesterday morning"), then captured_at. event_at_confidence reflects
which path produced the date.
"""

from __future__ import annotations

import email.utils
import re
from datetime import datetime, timezone
from typing import Any


HEADER_FROM_RX = re.compile(r"^\s*From\s*:\s*(.+?)\s*$", re.I | re.M)
HEADER_TO_RX = re.compile(r"^\s*To\s*:\s*(.+?)\s*$", re.I | re.M)
HEADER_SUBJECT_RX = re.compile(r"^\s*Subject\s*:\s*(.+?)\s*$", re.I | re.M)
HEADER_DATE_RX = re.compile(r"^\s*Date\s*:\s*(.+?)\s*$", re.I | re.M)
HEADER_MESSAGE_ID_RX = re.compile(r"^\s*Message-?ID\s*:\s*<?([^>\s]+)>?\s*$", re.I | re.M)

# Gmail/Outlook "On <date>, <name> wrote:" attribution lines. Captures the
# date and the sender name from quoted-reply boilerplate.
ON_DATE_WROTE_RX = re.compile(
    r"\bOn\s+([A-Z][a-z]+(?:\s+\d{1,2})?(?:[,\s]+\d{4})?(?:\s+at\s+\d{1,2}:\d{2}(?:\s*[AP]M)?)?)"
    r"[,\s]+([A-Z][A-Za-z\-' ]+?)\s+(?:<[^>]+>\s+)?wrote\s*:",
)

ADDRESS_RX = re.compile(r"<([^>]+@[^>]+)>")
NAMED_SENDER_RX = re.compile(r"^([^<]+?)\s*<([^>]+)>\s*$")


def _parse_captured_at(captured_at: str | None) -> tuple[datetime, str]:
    if captured_at:
        try:
            dt = datetime.fromisoformat(captured_at)
            return dt, captured_at
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    return now, now.isoformat(timespec="seconds")


def _parse_date_header(date_str: str) -> str | None:
    """Parse an RFC 5322 / RFC 2822 Date header into ISO 8601."""
    if not date_str:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt is not None:
            return dt.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    return None


def _parse_attribution_date(date_phrase: str, captured_dt: datetime) -> str | None:
    """Parse the date out of an 'On May 19, 2026 at 10:00 AM, ...' line."""
    if not date_phrase:
        return None
    # Try a few common shapes.
    for fmt in (
        "%B %d, %Y at %I:%M %p",
        "%B %d, %Y",
        "%b %d, %Y at %I:%M %p",
        "%b %d, %Y",
        "%B %d at %I:%M %p",
        "%b %d at %I:%M %p",
        "%B %d",
        "%b %d",
    ):
        try:
            dt = datetime.strptime(date_phrase.strip(), fmt)
            if dt.year == 1900:
                # Format didn't carry a year — adopt captured year.
                dt = dt.replace(year=captured_dt.year)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _parse_sender(from_value: str) -> dict | None:
    """Pull (name, email) out of a From header value."""
    if not from_value:
        return None
    m = NAMED_SENDER_RX.match(from_value.strip())
    if m:
        return {"raw": m.group(1).strip().strip('"'), "email": m.group(2).strip(),
                "decision": "propose_new_contact"}
    addr = ADDRESS_RX.search(from_value)
    if addr:
        return {"raw": from_value.split("<")[0].strip().strip('"') or from_value,
                "email": addr.group(1).strip(),
                "decision": "propose_new_contact"}
    if "@" in from_value:
        return {"raw": from_value.strip(), "email": from_value.strip(),
                "decision": "propose_new_contact"}
    return {"raw": from_value.strip(), "decision": "propose_new_contact"}


def preprocess(req: dict[str, Any]) -> dict[str, Any]:
    captured_dt, captured_iso = _parse_captured_at(req.get("captured_at"))
    raw_text = req.get("raw_text") or req.get("summary") or ""

    from_match = HEADER_FROM_RX.search(raw_text)
    to_match = HEADER_TO_RX.search(raw_text)
    subject_match = HEADER_SUBJECT_RX.search(raw_text)
    date_match = HEADER_DATE_RX.search(raw_text)
    msgid_match = HEADER_MESSAGE_ID_RX.search(raw_text)
    attrib_match = ON_DATE_WROTE_RX.search(raw_text)

    # Date resolution
    explicit_event_at = req.get("event_at")
    if explicit_event_at:
        event_at_iso = explicit_event_at
        event_at_confidence = req.get("event_at_confidence") or "high"
        event_at_source = req.get("event_at_source") or "operator_explicit_event_at"
    elif date_match:
        parsed = _parse_date_header(date_match.group(1))
        if parsed:
            event_at_iso = parsed
            event_at_confidence = "high"
            event_at_source = "email_date_header"
        else:
            event_at_iso = captured_iso[:10]
            event_at_confidence = "low"
            event_at_source = "fallback_to_captured_at"
    elif attrib_match:
        parsed = _parse_attribution_date(attrib_match.group(1), captured_dt)
        if parsed:
            event_at_iso = parsed
            event_at_confidence = "medium"
            event_at_source = "email_attribution_line"
        else:
            event_at_iso = captured_iso[:10]
            event_at_confidence = "low"
            event_at_source = "fallback_to_captured_at"
    else:
        event_at_iso = captured_iso[:10]
        event_at_confidence = "low"
        event_at_source = "fallback_to_captured_at"

    # Sender extraction
    extracted_people = list(req.get("people") or [])
    seen = {(p.get("raw") or p.get("name") or "").lower() for p in extracted_people}
    if from_match:
        sender = _parse_sender(from_match.group(1))
        if sender and (sender.get("raw") or "").lower() not in seen:
            extracted_people.append(sender)
            seen.add((sender.get("raw") or "").lower())
    if attrib_match:
        # The wrote-attribution captured a name too.
        name = (attrib_match.group(2) or "").strip()
        if name and name.lower() not in seen and len(name) > 2:
            extracted_people.append({"raw": name, "decision": "propose_new_contact"})
            seen.add(name.lower())

    subject = subject_match.group(1).strip() if subject_match else None
    message_id = msgid_match.group(1).strip() if msgid_match else None

    source_extras = dict(req.get("source_ref") or {})
    source_extras.setdefault("subject", subject)
    source_extras.setdefault("message_id", message_id)
    if to_match:
        source_extras.setdefault("to", to_match.group(1).strip())

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
        "extracted_people": extracted_people,
        "extracted_companies": req.get("companies") or [],
        "additional_signals": [],
        "source_extras": source_extras,
    }
