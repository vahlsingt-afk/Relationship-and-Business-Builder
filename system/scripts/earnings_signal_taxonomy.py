#!/usr/bin/env python3
"""earnings_signal_taxonomy.py — financial-health/momentum signal taxonomy
for real earnings-release/MD&A excerpt text (RB-2026-09-05, Predictive
Market Intelligence Phase 2).

Mirrors vulnerability_taxonomy.py's shape (a category dict of regex
patterns), but classifies a different kind of text for a different
purpose: vulnerability_taxonomy.py classifies third-party NEWS about a
brand/vendor (job postings, press coverage); this module classifies a
company's OWN earnings-release language about itself.

Scope, decided directly rather than assumed: an earlier attempt to build a
"vendor evaluation" / "shopping for a new solution" category against this
same excerpt corpus found ZERO real matches across all 730 real excerpts on
file -- companies don't publicly announce vendor shopping in PR-controlled
earnings releases, that signal already lives in vulnerability_taxonomy.py's
tech_hiring/exec_change_tech/vendor_relationship categories (sourced from
job postings and news, a fundamentally better-suited data source). Building
a taxonomy for language that doesn't appear in this corpus would be
infrastructure that looks wired but never fires -- a pattern this project
has hit and fixed before. Every category below was grounded in real,
counted hits against the actual corpus before being included; a fourth
candidate category (commodity/labor cost pressure) was tried and dropped
for the same reason (zero real hits with any tried phrasing).

What this DOES support: is a brand's own earnings language showing real
financial distress or real growth confidence right now -- useful context
for account planning (a distressed account is more budget-constrained; a
confident, growing account is a better target for new investment), and
potentially reusable by competitive_vulnerability.py for vendor-side
financial-distress evidence sourced from the vendor's own filings rather
than only third-party news.
"""
from __future__ import annotations

import re

FINANCIAL_HEALTH_CATEGORIES: dict[str, dict] = {
    "financial_distress": {
        "label": "Financial Distress",
        "pattern": re.compile(
            r"impairment|restructuring|layoffs?|workforce reduction|going concern|"
            r"covenant|goodwill (charge|impairment)|store closures?",
            re.IGNORECASE,
        ),
    },
    "growth_confidence": {
        "label": "Growth Confidence",
        "pattern": re.compile(
            r"accelerat(e|ing)|record (revenue|quarter|sales)|"
            r"expand(ing|ed) (our|footprint)|momentum continues|"
            r"unit growth|net new (openings|units)",
            re.IGNORECASE,
        ),
    },
    "traffic_pressure": {
        "label": "Traffic Pressure",
        "pattern": re.compile(
            r"traffic (declin|softness|decreas)|"
            r"comparable (sales|store sales) (declin|decreas|down)|"
            r"challenging (consumer|macro)|value.conscious|softer? demand",
            re.IGNORECASE,
        ),
    },
}


def classify_financial_health(text: str) -> list[str]:
    """Return the subset of FINANCIAL_HEALTH_CATEGORIES keys whose pattern
    matches somewhere in `text`. Never invents a category the text doesn't
    actually support -- an empty list is a valid, honest answer."""
    if not text:
        return []
    return [name for name, cat in FINANCIAL_HEALTH_CATEGORIES.items() if cat["pattern"].search(text)]
