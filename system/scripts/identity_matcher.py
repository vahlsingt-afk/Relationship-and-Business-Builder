#!/usr/bin/env python3
"""
identity_matcher.py — shared baseline identity-resolution primitives
(RB-DEFECT-064 Phase 2).

Every structured-source ingest script (linkedin_ingest.py, contacts_ingest.py,
hubspot_ingest.py) answers the same underlying question — "does this row
already exist in baseline_index.json?" — using whichever identifying fields
the source happens to provide (LinkedIn URL, email, phone, name). Each script
was independently reimplementing the same index-building and exact-key
matching. This module holds that shared infrastructure.

Deliberately NOT included here: source-specific matching *judgment* —
nickname expansion, composite company-evidence scoring, and priority order
between fields differ by source and stay in each script. This module builds
indexes and answers "is there exactly one match," not "should I trust this
match."
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------

def norm_name(name: str | None) -> str:
    """Whitespace/case-normalized name key. 'John  Smith' and 'john smith'
    collapse to the same key; 'John' and 'JohnSmith' do not."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def name_letters_key(name: str | None) -> str:
    """Letters-only name key — drops spaces and punctuation. Used where a
    source's name formatting is inconsistent enough that whitespace-only
    normalization isn't sufficient (e.g. Apple Contacts vs. baseline)."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def linkedin_slug(url: str | None) -> str | None:
    """Last path segment of a LinkedIn profile URL, lowercased."""
    if not url:
        return None
    url = url.strip().rstrip("/")
    if not url:
        return None
    return url.rsplit("/", 1)[-1].lower() or None


def normalize_phone(raw: str | None) -> str:
    """Normalize to a bare 10-digit US number (strips a leading +1)."""
    cleaned = re.sub(r"[^\d+]", "", str(raw or ""))
    if not cleaned:
        return ""
    if len(cleaned) == 11 and cleaned.startswith("1"):
        cleaned = cleaned[1:]
    if len(cleaned) < 7:
        return ""
    return cleaned


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def build_email_index(baseline: Iterable[dict], *, email_field: str = "email") -> dict[str, dict]:
    """One-to-one; last entry wins if the same email somehow appears twice
    (matches the plain-assignment behavior every source previously had)."""
    index: dict[str, dict] = {}
    for entry in baseline:
        email = (entry.get(email_field) or "").strip().lower()
        if email:
            index[email] = entry
    return index


def build_phone_index(baseline: Iterable[dict], *, phone_field: str = "phone") -> dict[str, dict]:
    """One-to-one; last entry wins on a duplicate phone number."""
    index: dict[str, dict] = {}
    for entry in baseline:
        phone = normalize_phone(entry.get(phone_field))
        if phone:
            index[phone] = entry
    return index


def build_linkedin_url_index(baseline: Iterable[dict], *, url_field: str = "linkedin_url") -> dict[str, dict]:
    """One-to-one; last entry wins on a duplicate URL slug."""
    index: dict[str, dict] = {}
    for entry in baseline:
        slug = linkedin_slug(entry.get(url_field))
        if slug:
            index[slug] = entry
    return index


def build_name_index(
    baseline: Iterable[dict], *, name_field: str = "name", key_fn=norm_name
) -> dict[str, list[dict]]:
    """One-to-many: names can repeat across baseline entries. Callers use
    `match_unique_name` to decide whether a name is safe to auto-match."""
    index: defaultdict[str, list[dict]] = defaultdict(list)
    for entry in baseline:
        key = key_fn(entry.get(name_field))
        if key:
            index[key].append(entry)
    return dict(index)


def build_name_index_single(
    baseline: Iterable[dict], *, name_field: str = "name", key_fn=name_letters_key
) -> dict[str, dict]:
    """One-to-one, last-write-wins. For sources whose existing matching logic
    (e.g. contacts_ingest.py's nickname/company escalation) predates
    ambiguity-aware name matching and expects a single candidate per key."""
    index: dict[str, dict] = {}
    for entry in baseline:
        key = key_fn(entry.get(name_field))
        if key:
            index[key] = entry
    return index


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_unique_name(
    name: str, name_index: dict[str, list[dict]], *, key_fn=norm_name
) -> tuple[dict | None, bool]:
    """Return (match, is_ambiguous).

    Only returns a match when exactly one baseline entry shares the name.
    When multiple entries share it, returns (None, True) — the caller should
    treat that as a duplicate candidate needing operator confirmation, never
    guess which one it means.
    """
    key = key_fn(name)
    if not key:
        return None, False
    candidates = name_index.get(key, [])
    if len(candidates) == 1:
        return candidates[0], False
    if len(candidates) > 1:
        return None, True
    return None, False
