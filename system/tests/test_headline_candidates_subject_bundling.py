"""
test_headline_candidates_subject_bundling.py

RB-DEFECT-2026-07-16: a multi-story newsletter's subject line (and, when
there's no HTML body to extract from, its snippet) bundles several
unrelated headlines in one string -- e.g. Payments Dive's "July 15 -
Stripe's $53B bid for PayPal | Does BNPL pump up prices?" or Restaurant
Dive's snippet "Chipotle expands into Mexico; Pizza Hut serves up
nostalgia...". _headline_candidates() included that raw bundle as its own
candidate -- since it packs in keywords from multiple stories at once, it
out-scored the real per-article titles in _score() and became the
"headline" attached to entities in it. This surfaced in the Watchlist/
Earnings sections as garbled, misattributed evidence: Section F's "Stripe"
escalation and Section E's "Chipotle" entry both showed a whole bundled
line (the Chipotle one was mostly about a McDonald's exec hire) instead of
a single coherent headline.

Fix: split subject and snippet into individual story fragments the same
way (now also on ";"), instead of treating either as one candidate. Body-
extracted titles are still preferred outright when available.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import passive_email_intelligence as pei  # noqa: E402


class TestHeadlineCandidatesSubjectBundling(unittest.TestCase):
    def test_bundled_subject_excluded_when_body_titles_available(self):
        thread = {
            "subject": "July 15 - Stripe's $53B bid for PayPal | Does BNPL pump up prices?",
            "snippet": "",
            "body_text": (
                '<a href="https://paymentsdive.com/stripe-paypal-bid">'
                "Stripe makes $53B bid for PayPal, sources say</a>"
                '<a href="https://paymentsdive.com/bnpl-prices">'
                "Does BNPL pump up prices for consumers?</a>"
            ),
        }
        candidates = pei._headline_candidates(thread)
        bundled_subject = "July 15 - Stripe's $53B bid for PayPal | Does BNPL pump up prices?"
        self.assertNotIn(bundled_subject, candidates)
        self.assertTrue(any("Stripe makes $53B bid for PayPal" in c for c in candidates))
        self.assertTrue(any("Does BNPL pump up prices" in c for c in candidates))

    def test_subject_still_used_as_fallback_when_no_body_titles_extracted(self):
        thread = {
            "subject": "Restaurant chain announces new drive-thru AI ordering system",
            "snippet": "",
            "body_text": "",
        }
        candidates = pei._headline_candidates(thread)
        self.assertIn(
            "Restaurant chain announces new drive-thru AI ordering system",
            candidates,
        )

    def test_chipotle_mcdonalds_bundle_does_not_surface_as_single_headline(self):
        thread = {
            "subject": "July 15 - McDonald's hires chief strategy officer | Chipotle enters Mexico",
            "snippet": "",
            "body_text": (
                '<a href="https://restaurantdive.com/mcdonalds-csos">'
                "McDonald's hires chief strategy officer from outside the industry</a>"
                '<a href="https://restaurantdive.com/chipotle-mexico">'
                "Chipotle enters Mexico with first company-owned restaurant</a>"
            ),
        }
        candidates = pei._headline_candidates(thread)
        bundled_subject = "July 15 - McDonald's hires chief strategy officer | Chipotle enters Mexico"
        self.assertNotIn(bundled_subject, candidates)
        self.assertTrue(any("Chipotle enters Mexico" in c for c in candidates))
        self.assertTrue(any("McDonald's hires chief strategy officer" in c for c in candidates))

    def test_bundled_subject_split_into_fragments_when_no_body_text_at_all(self):
        """Regression for the real Restaurant Dive thread: no body_text field
        at all (common for plain-ish newsletters), so the bundled subject
        must be split into fragments rather than used whole."""
        thread = {
            "subject": "July 15 - McDonald's hires chief strategy officer | Chipotle enters Mexico",
            "snippet": "",
            "body_text": "",
        }
        candidates = pei._headline_candidates(thread)
        bundled_subject = "July 15 - McDonald's hires chief strategy officer | Chipotle enters Mexico"
        self.assertNotIn(bundled_subject, candidates)
        self.assertTrue(any("McDonald's hires chief strategy officer" in c for c in candidates))
        self.assertTrue(any("Chipotle enters Mexico" in c for c in candidates))

    def test_semicolon_separated_snippet_split_into_fragments(self):
        thread = {
            "subject": "Restaurant Dive Daily Dive",
            "snippet": (
                "Chipotle expands into Mexico; Pizza Hut serves up nostalgia with "
                "latest rewards program push; Why restaurant AI setbacks aren't "
                "the whole story"
            ),
            "body_text": "",
        }
        candidates = pei._headline_candidates(thread)
        self.assertTrue(any("Chipotle expands into Mexico" in c for c in candidates))
        self.assertTrue(any("Pizza Hut serves up nostalgia" in c for c in candidates))
        self.assertTrue(any("Why restaurant AI setbacks" in c for c in candidates))


if __name__ == "__main__":
    unittest.main()
