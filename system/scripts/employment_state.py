#!/usr/bin/env python3
"""Shared date-aware employment-state resolution.

Used by every profile/enrichment adapter that needs to decide whether a
person's most recently listed role is still current. A role is active only
when its date range explicitly says Present/Current/Now; a stale first-listed
role with an explicit ended-date range must not persist as current employment
(RB — LinkedIn ended-role/current-company cleanup, 2026-08-06).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_CURRENT_ROLE_RX = re.compile(r"\b(present|current|now)\b", re.I)
_ENDED_ROLE_RX = re.compile(
    r"(?:-|–|—|\bto\b)\s*(?:[A-Za-z]{3,9}\s+)?(?:19|20)\d{2}\b",
    re.I,
)
_END_DATE_CAPTURE_RX = re.compile(
    r"(?:-|–|—|\bto\b)\s*(?:([A-Za-z]{3,9})\s+)?((?:19|20)\d{2})\b",
    re.I,
)
_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_end_date(dates: str | None) -> str | None:
    """Best-effort normalize the end token of a 'Jun 2021 - Mar 2025' style
    range to ISO YYYY-MM-DD. LinkedIn never supplies a day, so day is fixed
    at 01; month defaults to January when only a year is given."""
    if not dates:
        return None
    m = _END_DATE_CAPTURE_RX.search(dates)
    if not m:
        return None
    month_name, year = m.group(1), m.group(2)
    month = _MONTH_NUMBERS.get((month_name or "")[:3].lower(), 1)
    return f"{year}-{month:02d}-01"


def _employment_state(experience: list[dict]) -> dict:
    """Resolve active employment without treating a stale first role as current.

    LinkedIn profile captures include a human-readable ``dates`` value such as
    ``Apr 2022 - Present`` or ``Jan 2020 - Mar 2025``.  A role is active only
    when its date range explicitly says Present/Current/Now.  When at least one
    entry has explicit ended-role evidence and none is current, RB leaves the
    active company/title blank while retaining the first (most recent) entry as
    last-known employment.  Date-less captures preserve the legacy first-entry
    fallback because absence of dates is not evidence that the role ended.
    """
    if not experience:
        return {
            "status": "unknown",
            "current_company": None,
            "current_role": None,
            "last_known_company": None,
            "last_known_role": None,
            "last_known_dates": None,
            "definitive_no_current_role": False,
            "employment_end_date": None,
            "employment_date_confidence": "undated",
        }

    for item in experience:
        dates = (item.get("dates") or "").strip()
        if _CURRENT_ROLE_RX.search(dates):
            return {
                "status": "stated_current_role",
                "current_company": item.get("company"),
                "current_role": item.get("title"),
                "last_known_company": item.get("company"),
                "last_known_role": item.get("title"),
                "last_known_dates": dates or None,
                "definitive_no_current_role": False,
                "employment_end_date": None,
                "employment_date_confidence": "dated",
            }

    first = experience[0]
    dated_entries = [
        item for item in experience
        if _ENDED_ROLE_RX.search((item.get("dates") or "").strip())
    ]
    if dated_entries:
        last_known_dates = (first.get("dates") or "").strip() or None
        return {
            "status": "no_stated_current_role",
            "current_company": None,
            "current_role": None,
            "last_known_company": first.get("company"),
            "last_known_role": first.get("title"),
            "last_known_dates": last_known_dates,
            "definitive_no_current_role": True,
            "employment_end_date": _parse_end_date(last_known_dates),
            "employment_date_confidence": "dated",
        }

    return {
        "status": "current_role_date_unavailable",
        "current_company": first.get("company"),
        "current_role": first.get("title"),
        "last_known_company": first.get("company"),
        "last_known_role": first.get("title"),
        "last_known_dates": (first.get("dates") or "").strip() or None,
        "definitive_no_current_role": False,
        "employment_end_date": None,
        "employment_date_confidence": "undated",
    }


def resolve_operator_confirmed_departure(
    entry: dict, *, former_company: str, former_role: str | None = None,
    departed_since: str | None = None,
) -> dict:
    """Field updates for an operator-confirmed statement that a contact has
    left `former_company` -- e.g. Todd relaying on a call "Sal has been away
    from PAR since May 2026." Mirrors linkedin_session_reader.py's LinkedIn-
    profile-driven no_stated_current_role resolution (same field set, same
    "preserve history instead of silently keeping a stale current_company"
    semantics) so a human-reported departure gets identical treatment to a
    LinkedIn profile capture -- and, per employment_date_confidence's own
    documented outranking rule (baseline.schema.json), "operator_confirmed"
    outranks even a dated LinkedIn capture.

    Returns {} (no-op) unless `former_company` actually matches the entry's
    current_company (case-insensitive) -- a departure statement about some
    OTHER employer must not blank out an unrelated current role. `entry` is
    read-only here; the caller applies the returned dict.
    """
    current = (entry.get("current_company") or "").strip().lower()
    if not former_company or not current or current != former_company.strip().lower():
        return {}
    return {
        "current_company": None,
        "current_role": None,
        "employment_status": "no_stated_current_role",
        "employment_status_source": "operator_confirmed",
        "employment_status_observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "employment_end_date": departed_since,
        "employment_date_confidence": "operator_confirmed",
        "last_known_company": entry.get("current_company"),
        "last_known_role": former_role or entry.get("current_role"),
    }


def is_employment_state_protected(entry: dict) -> bool:
    """True when an entry's employment state was date-resolved to no current
    role and must not be silently overwritten by an undated source (e.g. a
    stale LinkedIn Connections.csv row that still lists the ended company).
    """
    if entry.get("employment_status") == "no_stated_current_role":
        return True
    return "linkedin_no_stated_current_role" in (entry.get("tags") or [])
