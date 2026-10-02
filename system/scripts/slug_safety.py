"""slug_safety.py — path-traversal guard (CWE-22) for slug-keyed portfolio dirs.

RB-SECURITY-2026-09-05, full ingest-route audit: found that Blue Sheets'
createBlueSheetAccount (POST /blue-sheets, body.account_slug) and Master
Account Plans' ingestMasterAccountPlanUpload (POST /master-account-plans/
{vendor_slug}/ingest, path param) both build a filesystem path directly
from a caller-supplied slug with NO validation anywhere in the call chain
-- `ROOT / "accounts" / slug` / `ROOT / "vendors" / vendor_slug` -- then
unconditionally `mkdir(parents=True)` and write JSON content into it. A
slug containing '..'/'/' could escape the intended portfolio directory
entirely; both routes are gated only by the same shared x-api-key every
other write route uses, not a stronger boundary.

Competitor Intelligence's equivalent creation path (createCompetitor ->
generate_profile -> ensure_competitor -> resolve_competitor) was already
safe end-to-end: it always derives the slug via _slugify() (competitor_
intelligence.py) before use, never accepting a raw caller-supplied slug.
This module gives Blue Sheets and Master Account Plans the same guarantee,
and hardens Competitor Intelligence's own low-level competitor_dir() too
so any FUTURE caller gets it for free rather than depending on every call
site remembering to slugify first.

Every slug this system actually generates (via _slugify()-style functions
in competitor_intelligence.py and elsewhere) already produces exactly this
shape: lowercase letters/digits joined by single hyphens. Real accounts
like "pollo-campero", "mcdonalds", "worldpay" all match; nothing legitimate
is rejected.
"""
from __future__ import annotations

import re

_SAFE_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def assert_safe_slug(slug: str, *, label: str = "slug") -> None:
    """Raises ValueError if `slug` isn't a safe lowercase-alphanumeric-
    hyphen token. Call this BEFORE building any filesystem path from a
    caller-supplied slug -- never after."""
    if not isinstance(slug, str) or not slug or not _SAFE_SLUG_RE.match(slug):
        raise ValueError(
            f"{label} {slug!r} is not a valid slug -- must be lowercase letters, "
            "digits, and single hyphens only (e.g. 'mcdonalds', 'pollo-campero')."
        )
